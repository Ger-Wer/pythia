"""Run on amper only: python -m unittest -v test_paper_ledger."""

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from python.evaluation.paper_ledger import PaperLedger


class PaperLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "paper.sqlite"
        self.ledger = PaperLedger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def forecast(self, market="m1", event="e1", revision="1", p="0.7", baseline="0.5", **kwargs):
        return self.ledger.record_forecast(event, market, "model", revision, p, baseline,
                                           issued_at=1000, closes_at=2000, now=1000, **kwargs)

    def buy(self, forecast, **kwargs):
        options = dict(side="YES", asks=[("0.5", "1000")], quoted_at=1010,
                       now=1010, max_price="0.55", budget="10", fee_rate="0")
        options.update(kwargs)
        return self.ledger.buy(forecast, **options)

    def test_forecast_immutable_retry_and_metadata(self):
        f = self.forecast(metadata={"title": "CPI", "decision": "SKIP"})
        self.assertEqual(f, self.forecast(metadata={"decision": "SKIP", "title": "CPI"}))
        with self.assertRaises(ValueError):
            self.forecast(metadata={"title": "different"})
        self.assertEqual(self.ledger.list_forecasts()[0]["metadata"]["title"], "CPI")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute("DELETE FROM forecasts")

    def test_backdated_and_future_forecasts_rejected(self):
        for issued, close, now in ((1000, 2000, 2000), (1100, 2000, 1000), (1000, 3000, 1400)):
            with self.assertRaises(ValueError):
                self.ledger.record_forecast("e", "m", "v", "1", ".5", ".5", issued, close, now=now)

    def test_fill_cash_fees_settlement_and_restart_idempotence(self):
        f = self.forecast()
        trade = self.buy(f, fee_rate="0.05")
        self.assertEqual(trade["status"], "filled")
        self.assertLessEqual(Decimal(trade["total"]), Decimal("10"))
        self.assertGreater(Decimal(trade["fee"]), 0)
        self.assertEqual(self.buy(f)["id"], trade["id"])
        self.assertEqual(self.ledger.report()["open"], 1)
        self.ledger.settle("m1", "YES", 2000, now=2001)
        expected = Decimal("1000") - Decimal(trade["total"]) + Decimal(trade["shares"])
        self.assertEqual(Decimal(self.ledger.report()["cash"]), expected)
        self.ledger.close()
        self.ledger = PaperLedger(self.path)
        self.assertEqual(self.ledger.settle("m1", "YES", 2000, now=3000)["status"], "already_settled")
        self.assertEqual(Decimal(self.ledger.report()["cash"]), expected)
        with self.assertRaises(ValueError):
            self.ledger.settle("m1", "NO", 2000, now=3000)

    def test_no_synthetic_fill_for_depth_price_or_stale_quotes(self):
        f = self.forecast()
        cases = [({"asks": [("0.5", "1")]}, "insufficient_depth"),
                 ({"asks": [("0.7", "1000")]}, "price_limit"),
                 ({"quoted_at": 999}, "stale_or_nonprospective_quote"),
                 ({"quoted_at": 1000, "now": 999}, "stale_or_nonprospective_quote"),
                 ({"now": 1200}, "stale_or_nonprospective_quote")]
        for options, reason in cases:
            self.assertEqual(self.buy(f, **options)["reason"], reason)
        self.assertEqual(self.ledger.report()["cash"], "1000")
        self.assertEqual(self.ledger.report()["open"], 0)
        self.assertEqual(self.ledger.report()["skip_reasons"]["stale_or_nonprospective_quote"], 1)

    def test_multilevel_slippage_and_side_correctness(self):
        f = self.forecast()
        self.assertEqual(self.buy(f, side="NO", asks=[("0.49", "10"), ("0.52", "1000")])["reason"], "price_limit")
        trade = self.buy(f, side="NO", asks=[("0.49", "10"), ("0.50", "1000")])
        self.assertEqual(len(trade["levels"]), 2)
        self.ledger.settle("m1", "NO", 2000, now=2000)
        self.assertGreater(Decimal(self.ledger.report()["realised_pnl"]), 0)

    def test_minimum_order_size_is_shares_not_dollars(self):
        trade = self.buy(self.forecast(), asks=[("0.2", "1000")], min_shares="20")
        self.assertEqual(trade["status"], "filled")
        self.assertEqual(Decimal(trade["shares"]), Decimal("50"))
        cash = self.ledger.report()["cash"]
        skipped = self.buy(self.forecast("m2", "e2"), asks=[("0.8", "1000")],
                           max_price="0.9", min_shares="20")
        self.assertEqual(skipped["reason"], "below_minimum_order_size")
        self.assertEqual(self.ledger.report()["cash"], cash)
        self.assertEqual(self.ledger.report()["open"], 1)

    def test_risk_limits_cover_market_event_and_total(self):
        self.assertEqual(self.buy(self.forecast(), budget="51")["reason"], "position_limit")
        self.assertEqual(self.buy(self.forecast(), budget="50")["status"], "filled")
        self.assertEqual(self.buy(self.forecast(revision="2"), budget="1")["reason"], "position_limit")
        self.buy(self.forecast("m2"), budget="50")
        self.assertEqual(self.buy(self.forecast("event_blocked"), budget="1")["reason"], "event_limit")
        for number in range(3, 6):
            self.buy(self.forecast(f"m{number}", f"e{number}"), budget="50")
        self.assertEqual(self.buy(self.forecast("m6", "e6"), budget="1")["reason"], "total_limit")

    def test_void_refund_split_and_realised_drawdown(self):
        for market, outcome in (("loss", "NO"), ("void", "VOID"), ("split", "SPLIT")):
            self.buy(self.forecast(market, market), fee_rate="0.05" if outcome == "VOID" else "0")
            self.ledger.settle(market, outcome, 2000, now=2000)
        report = self.ledger.report()
        self.assertEqual(Decimal(report["realised_pnl"]), Decimal("-10"))
        self.assertEqual(Decimal(report["realised_drawdown"]), Decimal("10"))
        self.assertEqual(report["voids"], 1)
        self.assertEqual(report["closed"], 3)
        self.assertEqual(report["open"], 0)

    def test_brier_includes_skips_uses_first_revision_and_independent_events(self):
        f = self.forecast(p="0.8", baseline="0.5")
        self.ledger.record_skip(f, "no_edge", now=1010)
        self.forecast(revision="2", p="0.99")
        self.forecast("m2", "e1", p="0.6", baseline="0.4")
        self.ledger.settle("m1", "YES", 2000, now=2000)
        self.ledger.settle("m2", "NO", 2000, now=2000)
        report = self.ledger.report()
        self.assertEqual(report["independent_events"], 1)
        self.assertEqual(report["brier_by_model"]["model"]["count"], 2)
        self.assertEqual(Decimal(report["brier_by_model"]["model"]["model"]), Decimal("0.20"))
        self.assertEqual(Decimal(report["brier_by_model"]["model"]["baseline"]), Decimal("0.205"))


if __name__ == "__main__":
    unittest.main()
