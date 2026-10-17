"""Local paper accounting only; no network, wallet or order-signing dependencies.

Budget and exposure include taker fees. Quotes are asks for the selected side.
Fills require the entire requested budget (apart from <$0.000011 rounding dust).
Equity uses open acquisition cost, NOT a mark-to-market valuation.
VOID refunds cost AND fee by simulator convention; SPLIT pays 0.50 per share.
All timestamps are epoch seconds or timezone-aware ISO-8601 strings.
"""

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def dec(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("Non-finite decimal")
    return value


def stamp(value):
    if isinstance(value, str) and not value.replace(".", "", 1).isdigit():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Timestamp needs timezone")
        value = parsed.timestamp()
    return int(dec(value))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


class PaperLedger:
    SHARE_UNIT = Decimal("0.000001")
    FEE_UNIT = Decimal("0.00001")
    SPEND_DUST = Decimal("0.000011")

    def __init__(self, path, initial_bankroll="1000", max_position_fraction="0.05",
                 max_event_fraction="0.10", max_total_fraction="0.25"):
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS forecasts (
                id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, market_id TEXT NOT NULL,
                model TEXT NOT NULL, revision TEXT NOT NULL, probability TEXT NOT NULL,
                baseline TEXT NOT NULL, issued_at INTEGER NOT NULL, closes_at INTEGER NOT NULL,
                recorded_at INTEGER NOT NULL, metadata TEXT NOT NULL,
                UNIQUE(event_id, market_id, model, revision));
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY, forecast_id INTEGER UNIQUE NOT NULL REFERENCES forecasts(id),
                side TEXT NOT NULL, shares TEXT NOT NULL, cost TEXT NOT NULL,
                fee TEXT NOT NULL, at INTEGER NOT NULL, levels TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS settlements (
                id INTEGER PRIMARY KEY, market_id TEXT UNIQUE NOT NULL,
                outcome TEXT NOT NULL, at INTEGER NOT NULL, recorded_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS skips (
                id INTEGER PRIMARY KEY, forecast_id INTEGER NOT NULL REFERENCES forecasts(id),
                reason TEXT NOT NULL, at INTEGER NOT NULL, UNIQUE(forecast_id, reason));
        """)
        config = {"initial_bankroll": dec(initial_bankroll),
                  "max_position_fraction": dec(max_position_fraction),
                  "max_event_fraction": dec(max_event_fraction),
                  "max_total_fraction": dec(max_total_fraction)}
        if config["initial_bankroll"] <= 0 or any(
            not 0 < value <= 1 for key, value in config.items() if key != "initial_bankroll"
        ):
            raise ValueError("Invalid bankroll or risk limits")
        with self._tx():
            for key, value in config.items():
                old = self.db.execute("SELECT value FROM settings WHERE name=?", (key,)).fetchone()
                if old and dec(old[0]) != value:
                    raise ValueError("Cannot change existing ledger configuration")
                self.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, str(value)))
        self.config = config
        for table in ("settings", "forecasts", "positions", "settlements", "skips"):
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} "
                                f"BEFORE {operation} ON {table} BEGIN "
                                "SELECT RAISE(ABORT, 'append-only ledger'); END")

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def close(self):
        self.db.close()

    def record_forecast(self, event_id, market_id, model, revision, probability,
                        baseline, issued_at, closes_at, metadata=None, now=None,
                        max_recording_lag=300):
        """Append prospective forecast; altered retries of its immutable key fail."""
        now = stamp(time.time() if now is None else now)
        issued_at, closes_at = stamp(issued_at), stamp(closes_at)
        p, baseline = dec(probability), dec(baseline)
        if not 0 <= p <= 1 or not 0 <= baseline <= 1:
            raise ValueError("Probability must be between zero and one")
        key = tuple(str(item) for item in (event_id, market_id, model, revision))
        if any(not item.strip() for item in key):
            raise ValueError("Empty forecast identity")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("Forecast metadata must be an object")
        payload = (str(p.normalize()), str(baseline.normalize()), issued_at, closes_at, canonical(metadata or {}))
        with self._tx():
            old = self.db.execute("SELECT * FROM forecasts WHERE event_id=? AND market_id=? "
                                  "AND model=? AND revision=?", key).fetchone()
            if old:
                previous = tuple(old[name] for name in
                                 ("probability", "baseline", "issued_at", "closes_at", "metadata"))
                if previous != payload:
                    raise ValueError("Forecast identity already has different payload")
                return old["id"]
            if not issued_at <= now < closes_at or now - issued_at > max_recording_lag:
                raise ValueError("Forecast is not freshly recorded before the event")
            existing = self.db.execute("SELECT event_id, closes_at FROM forecasts WHERE market_id=?",
                                       (str(market_id),)).fetchone()
            if existing and (existing[0], existing[1]) != (str(event_id), closes_at):
                raise ValueError("Market event or close timestamp changed")
            if self.db.execute("SELECT 1 FROM settlements WHERE market_id=?", (str(market_id),)).fetchone():
                raise ValueError("Market already settled")
            cursor = self.db.execute("INSERT INTO forecasts (event_id,market_id,model,revision,"
                                     "probability,baseline,issued_at,closes_at,metadata,recorded_at) "
                                     "VALUES (?,?,?,?,?,?,?,?,?,?)", key + payload + (now,))
            return cursor.lastrowid

    def get_forecast(self, forecast_id):
        row = self.db.execute("SELECT * FROM forecasts WHERE id=?", (forecast_id,)).fetchone()
        if row is None:
            raise KeyError(forecast_id)
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def list_forecasts(self):
        return [self.get_forecast(row[0]) for row in self.db.execute("SELECT id FROM forecasts ORDER BY id")]

    def _skip(self, forecast_id, reason, now):
        self.db.execute("INSERT OR IGNORE INTO skips(forecast_id,reason,at) VALUES (?,?,?)",
                        (forecast_id, reason, now))
        return {"status": "skipped", "forecast_id": forecast_id, "reason": reason}

    def record_skip(self, forecast_id, reason, now=None):
        self.get_forecast(forecast_id)
        if not str(reason).strip():
            raise ValueError("Skip reason required")
        with self._tx():
            return self._skip(forecast_id, str(reason), stamp(time.time() if now is None else now))

    def _positions(self):
        return [dict(row) for row in self.db.execute(
            "SELECT p.*, f.event_id, f.market_id FROM positions p "
            "JOIN forecasts f ON f.id=p.forecast_id ORDER BY p.id")]

    @staticmethod
    def _payout(position, outcome):
        if outcome == "VOID":
            return dec(position["cost"]) + dec(position["fee"])
        return dec(position["shares"]) * (Decimal("0.5") if outcome == "SPLIT"
                                          else Decimal(position["side"] == outcome))

    def _state(self):
        positions = self._positions()
        settled = {row["market_id"]: row["outcome"] for row in self.db.execute("SELECT * FROM settlements")}
        cash = self.config["initial_bankroll"]
        for position in positions:
            cash -= dec(position["cost"]) + dec(position["fee"])
            if position["market_id"] in settled:
                cash += self._payout(position, settled[position["market_id"]])
        return cash, positions, settled

    def _trade(self, position):
        result = dict(position)
        result["status"] = "filled"
        result["total"] = str(dec(result["cost"]) + dec(result["fee"]))
        result["average_price"] = str(dec(result["cost"]) / dec(result["shares"]))
        result["levels"] = json.loads(result["levels"])
        return result

    def buy(self, forecast_id, side, asks, quoted_at, max_price, budget, fee_rate,
            now=None, max_quote_age=60, max_slippage="0.02", min_shares="0"):
        """All-or-none taker simulation. Depth, price and risk failures append a skip."""
        side = str(side).upper()
        if side not in ("YES", "NO"):
            raise ValueError("Side must be YES or NO")
        now, quoted_at = stamp(time.time() if now is None else now), stamp(quoted_at)
        budget, fee_rate, cap, slippage = map(dec, (budget, fee_rate, max_price, max_slippage))
        minimum = dec(min_shares)
        if budget <= 0 or not 0 <= fee_rate <= 1 or not 0 < cap < 1 or slippage < 0 or minimum < 0:
            raise ValueError("Invalid execution parameters")
        with self._tx():
            forecast = self.get_forecast(forecast_id)
            previous = self.db.execute("SELECT * FROM positions WHERE forecast_id=?", (forecast_id,)).fetchone()
            if previous:
                if previous["side"] != side:
                    raise ValueError("Existing trade has different side")
                return self._trade(previous)
            reason = None
            if (not forecast["issued_at"] <= quoted_at <= now or now < forecast["recorded_at"]
                    or now - quoted_at > max_quote_age):
                reason = "stale_or_nonprospective_quote"
            elif now >= forecast["closes_at"]:
                reason = "market_closed"
            cash, positions, settled = self._state()
            opened = [p for p in positions if p["market_id"] not in settled]
            exposure = sum((dec(p["cost"]) + dec(p["fee"]) for p in opened), Decimal(0))
            event_exposure = sum((dec(p["cost"]) + dec(p["fee"]) for p in opened
                                  if p["event_id"] == forecast["event_id"]), Decimal(0))
            market_exposure = sum((dec(p["cost"]) + dec(p["fee"]) for p in opened
                                   if p["market_id"] == forecast["market_id"]), Decimal(0))
            equity = cash + exposure
            if forecast["market_id"] in settled:
                reason = reason or "market_settled"
            elif budget > cash:
                reason = reason or "insufficient_cash"
            elif market_exposure + budget > equity * self.config["max_position_fraction"]:
                reason = reason or "position_limit"
            elif event_exposure + budget > equity * self.config["max_event_fraction"]:
                reason = reason or "event_limit"
            elif exposure + budget > equity * self.config["max_total_fraction"]:
                reason = reason or "total_limit"
            if reason:
                return self._skip(forecast_id, reason, now)
            levels = sorted((dec(price), dec(size)) for price, size in asks)
            if any(not 0 < price < 1 or size <= 0 for price, size in levels):
                raise ValueError("Invalid orderbook level")
            if not levels:
                return self._skip(forecast_id, "empty_orderbook", now)
            limit = min(cap, levels[0][0] + slippage)
            remaining, shares, cost, fees, fills = budget, Decimal(0), Decimal(0), Decimal(0), []
            for price, available in levels:
                if price > limit:
                    break
                unit = price + fee_rate * price * (1 - price)
                quantity = min(available, remaining / unit).quantize(self.SHARE_UNIT, rounding=ROUND_DOWN)
                fee = (quantity * fee_rate * price * (1 - price)).quantize(self.FEE_UNIT, rounding=ROUND_HALF_UP)
                while quantity > 0 and quantity * price + fee > remaining:
                    quantity -= self.SHARE_UNIT
                    fee = (quantity * fee_rate * price * (1 - price)).quantize(self.FEE_UNIT, rounding=ROUND_HALF_UP)
                if quantity <= 0:
                    continue
                spent = quantity * price
                shares, cost, fees = shares + quantity, cost + spent, fees + fee
                remaining -= spent + fee
                fills.append({"price": str(price), "shares": str(quantity), "fee": str(fee)})
                if remaining < self.SPEND_DUST:
                    break
            if shares == 0 or remaining >= self.SPEND_DUST:
                reason = "price_limit" if any(p > limit for p, _ in levels) else "insufficient_depth"
                return self._skip(forecast_id, reason, now)
            if shares < minimum:
                return self._skip(forecast_id, "below_minimum_order_size", now)
            cursor = self.db.execute("INSERT INTO positions(forecast_id,side,shares,cost,fee,at,levels) "
                                     "VALUES (?,?,?,?,?,?,?)", (forecast_id, side, str(shares), str(cost),
                                                               str(fees), now, canonical(fills)))
            return self._trade(self.db.execute("SELECT * FROM positions WHERE id=?", (cursor.lastrowid,)).fetchone())

    def settle(self, market_id, outcome, settled_at, now=None):
        outcome, market_id = str(outcome).upper(), str(market_id)
        now, settled_at = stamp(time.time() if now is None else now), stamp(settled_at)
        if outcome not in ("YES", "NO", "VOID", "SPLIT") or settled_at > now:
            raise ValueError("Invalid settlement")
        with self._tx():
            old = self.db.execute("SELECT * FROM settlements WHERE market_id=?", (market_id,)).fetchone()
            if old:
                if (old["outcome"], old["at"]) != (outcome, settled_at):
                    raise ValueError("Conflicting immutable settlement")
                return {"status": "already_settled", "market_id": market_id}
            forecast = self.db.execute("SELECT MAX(recorded_at) FROM forecasts WHERE market_id=?", (market_id,)).fetchone()[0]
            last_fill = self.db.execute("SELECT MAX(p.at) FROM positions p JOIN forecasts f ON f.id=p.forecast_id "
                                        "WHERE f.market_id=?", (market_id,)).fetchone()[0]
            if forecast is None or settled_at < max(forecast, last_fill or forecast):
                raise ValueError("Unknown market or settlement predates forecast/trade")
            self.db.execute("INSERT INTO settlements(market_id,outcome,at,recorded_at) VALUES (?,?,?,?)",
                            (market_id, outcome, settled_at, now))
            return {"status": "settled", "market_id": market_id, "outcome": outcome}

    def report(self):
        with self._tx():
            return self._report()

    def _report(self):
        cash, positions, settled = self._state()
        closed = [p for p in positions if p["market_id"] in settled]
        opened = [p for p in positions if p["market_id"] not in settled]
        pnl = {p["id"]: self._payout(p, settled[p["market_id"]]) - dec(p["cost"]) - dec(p["fee"])
               for p in closed}
        running = high = self.config["initial_bankroll"]
        drawdown = Decimal(0)
        for settlement in self.db.execute("SELECT market_id FROM settlements ORDER BY id"):
            running += sum((pnl[p["id"]] for p in closed if p["market_id"] == settlement[0]), Decimal(0))
            high = max(high, running)
            drawdown = max(drawdown, high - running)
        brier, events = {}, set()
        first = self.db.execute("SELECT f.*, s.outcome FROM forecasts f JOIN settlements s ON s.market_id=f.market_id "
                                "WHERE s.outcome IN ('YES','NO') AND f.id=(SELECT MIN(g.id) FROM forecasts g "
                                "WHERE g.event_id=f.event_id AND g.market_id=f.market_id AND g.model=f.model)")
        for f in first:
            events.add(f["event_id"])
            group = brier.setdefault(f["model"], {"count": 0, "model_sum": Decimal(0), "baseline_sum": Decimal(0)})
            outcome = Decimal(f["outcome"] == "YES")
            group["count"] += 1
            group["model_sum"] += (dec(f["probability"]) - outcome) ** 2
            group["baseline_sum"] += (dec(f["baseline"]) - outcome) ** 2
        for group in brier.values():
            group["model"] = str(group.pop("model_sum") / group["count"])
            group["baseline"] = str(group.pop("baseline_sum") / group["count"])
        exposure = sum((dec(p["cost"]) + dec(p["fee"]) for p in opened), Decimal(0))
        return {"cash": str(cash), "open_cost_including_fees": str(exposure),
                "cost_basis_equity": str(cash + exposure), "realised_pnl": str(sum(pnl.values(), Decimal(0))),
                "realised_drawdown": str(drawdown), "open": len(opened), "closed": len(closed),
                "wins": sum(value > 0 for value in pnl.values()), "losses": sum(value < 0 for value in pnl.values()),
                "voids": sum(settled[p["market_id"]] == "VOID" for p in closed),
                "independent_events": len(events), "brier_by_model": brier,
                "skip_reasons": dict(self.db.execute("SELECT reason,COUNT(*) FROM skips GROUP BY reason"))}
