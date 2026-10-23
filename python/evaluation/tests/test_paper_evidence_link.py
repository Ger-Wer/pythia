"""Run on amper only: python -m unittest -v test_paper_evidence_link."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from python.evaluation.paper_evidence_link import (
    LinkStore, dry_run, link_all, read_forecasts, verify_all, build_chain)
from python.evaluation.paper_ledger import PaperLedger
from python.evaluation.paper_telegram import Outbox

TEXT = "PAPER — учебная публикация\nПрогноз: test"
MEETING = "2026-10-28"
MANIFEST = {"meetings": {MEETING: {
    "dossier_ref": "dossier:fomc-2026-10-28",
    "claim_ids": ["claim-1", "claim-2"],
    "jev_verdict_ref": "jev:batch-2026-10-23",
    "snapshot_origins": ["r6:fomc-2026-10-28:t-3d"],
}}}


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class EvidenceLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.ledger = PaperLedger(self.state / "ledger.sqlite3")
        self.box = Outbox(self.state / "outbox.sqlite3", destination="-1001")

    def tearDown(self):
        self.tmp.cleanup()

    def forecast(self, market="m1", p="0.7"):
        metadata = {
            "meeting": MEETING, "condition_id": f"cond-{market}",
            "inputs": {"model": "zq-effr-25bp-v1",
                       "effr": {"effective_date": "2026-10-22", "rate": "4.08"},
                       "zq": {"asof": "2026-10-23", "contract": "ZQV6"}},
            "quote": {"midpoint": "0.50"},
        }
        return self.ledger.record_forecast(
            "e1", market, "zq-effr-25bp-v1", "2026-10-23", p, "0.5",
            issued_at=1000, closes_at=2000, metadata=metadata, now=1000)

    def buy(self, fid):
        return self.ledger.buy(fid, side="YES", asks=[("0.5", "1000")],
                               quoted_at=1010, now=1010, max_price="0.55",
                               budget="10", fee_rate="0")

    def post(self, fid, state="confirmed", message_id=555):
        key = f"forecast:{fid}"
        self.box.enqueue(key, TEXT, expires_at=2000)
        self.box.db.execute(
            "UPDATE outbox SET state=?, message_id=? WHERE key=?",
            (state, message_id, key))
        self.box.db.commit()

    def close_state(self):
        self.box.close()
        self.ledger.close()

    def links(self, sql, args=()):
        db = sqlite3.connect(self.state / "evidence_links.sqlite3")
        try:
            return db.execute(sql, args).fetchall()
        finally:
            db.close()

    def test_full_chain_win(self):
        fid = self.forecast()
        self.buy(fid)
        self.post(fid)
        self.ledger.settle("m1", "YES", 2000, now=2001)
        self.box.enqueue(f"result:{fid}", TEXT)
        self.close_state()
        counts = link_all(self.state, MANIFEST, now=3000)
        self.assertEqual(counts["linked"], 1)
        chain = build_chain(self.state, read_forecasts(self.state)[0])
        self.assertTrue(chain["complete"], chain)
        self.assertEqual(chain["execution"]["kind"], "fill")
        self.assertEqual(chain["post"]["state"], "confirmed")
        self.assertEqual(chain["outcome"]["outcome"], "YES")
        self.assertTrue(chain["snapshot_ref"].startswith("sha256:"))
        self.assertEqual(chain["evidence"]["dossier_ref"],
                         "dossier:fomc-2026-10-28")
        origins = json.loads(self.links("SELECT origins FROM snapshots")[0][0])
        self.assertEqual(origins, ["r6:fomc-2026-10-28:t-3d"])

    def test_loss_is_in_the_chain(self):
        fid = self.forecast()
        self.buy(fid)
        self.post(fid)
        self.ledger.settle("m1", "NO", 2000, now=2001)
        self.box.enqueue(f"result:{fid}", TEXT)
        self.close_state()
        link_all(self.state, MANIFEST, now=3000)
        chain = build_chain(self.state, read_forecasts(self.state)[0])
        self.assertTrue(chain["complete"], chain)
        self.assertEqual(chain["execution"]["side"], "YES")
        self.assertEqual(chain["outcome"]["outcome"], "NO")

    def test_skip_and_missing_post_are_explicit(self):
        fid = self.forecast()
        self.ledger.record_skip(fid, "insufficient_edge", now=1100)
        self.close_state()
        link_all(self.state, MANIFEST, now=3000)
        chain = build_chain(self.state, read_forecasts(self.state)[0])
        self.assertEqual(chain["execution"]["kind"], "skip")
        self.assertIn("post", chain["missing"])
        self.assertIn("outcome", chain["missing"])
        self.assertFalse(chain["complete"])

    def test_uncertain_delivery_surfaces(self):
        fid = self.forecast()
        self.buy(fid)
        self.post(fid, state="uncertain", message_id=None)
        self.close_state()
        link_all(self.state, MANIFEST, now=3000)
        chain = build_chain(self.state, read_forecasts(self.state)[0])
        self.assertEqual(chain["post"]["state"], "uncertain")
        self.assertNotIn("post", chain["missing"])

    def test_unlinked_without_dossier(self):
        self.forecast()
        self.close_state()
        counts = link_all(self.state, {}, now=3000)
        self.assertEqual(counts["unlinked"], 1)
        row = self.links(
            "SELECT state, reason_code, snapshot_ref FROM forecast_links")[0]
        self.assertEqual(row[0], "unlinked")
        self.assertEqual(row[1], "dossier_missing")
        self.assertTrue(row[2].startswith("sha256:"))

    def test_idempotent_and_no_rewrite(self):
        fid = self.forecast()
        self.buy(fid)
        self.close_state()
        before = (file_sha(self.state / "ledger.sqlite3"),
                  file_sha(self.state / "outbox.sqlite3"))
        link_all(self.state, MANIFEST, now=3000)
        counts = link_all(self.state, MANIFEST, now=4000)
        after = (file_sha(self.state / "ledger.sqlite3"),
                 file_sha(self.state / "outbox.sqlite3"))
        self.assertEqual(before, after)
        self.assertEqual(counts["snapshots"], 0)
        self.assertEqual(len(self.links("SELECT * FROM forecast_links")), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            db = sqlite3.connect(self.state / "evidence_links.sqlite3")
            db.execute("DELETE FROM forecast_links")
            db.close()

    def test_chain_cli(self):
        from python.evaluation.paper_evidence_link import main
        import io
        from contextlib import redirect_stdout
        fid = self.forecast()
        self.buy(fid)
        self.close_state()
        link_all(self.state, MANIFEST, now=3000)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["chain", "--state-dir", str(self.state),
                       "--forecast-id", str(fid)])
        self.assertEqual(rc, 0)
        chain = json.loads(out.getvalue())
        self.assertEqual(chain["forecast_id"], fid)
        self.assertEqual(chain["execution"]["kind"], "fill")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = main(["chain", "--state-dir", str(self.state),
                       "--forecast-id", "999"])
        self.assertEqual(rc, 1)

    def test_dry_run_reproducible_and_clean(self):
        fid = self.forecast()
        self.buy(fid)
        self.post(fid)
        self.close_state()
        first = dry_run(self.state, MANIFEST, now=3000)
        second = dry_run(self.state, MANIFEST, now=3000)
        self.assertEqual(first["digest"], second["digest"])
        self.assertEqual(first["link_counts"]["linked"], 1)
        self.assertFalse((self.state / "evidence_links.sqlite3").exists())
        report = verify_all(self.state)
        self.assertIn("evidence_link", report["chains"][0]["missing"])


if __name__ == "__main__":
    unittest.main()
