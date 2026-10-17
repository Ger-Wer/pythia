"""Paper experiment: public data -> immutable forecast -> simulated fill -> Telegram.

All Polymarket requests are GETs. No wallet, signature, trading credentials, or
live-order code is accepted here. Use --send plus explicit environment opt-in to
publish to the existing channel. Run only on amper under the project constitution.
"""

import argparse
import calendar
import fcntl
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from python.evaluation.paper_ledger import PaperLedger, dec
from python.evaluation.paper_market import DataError, entry_limit, final_outcome, get_book, load_hold_market
from python.evaluation.paper_sources import fetch_model_inputs, next_meeting
from python.evaluation.paper_telegram import Outbox, render_forecast, render_result, render_status

DEFAULT_STATE = Path(__file__).resolve().parent / "data" / "paper"


def iso(value):
    return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def save_json(path, value):
    raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def position_for(ledger, forecast_id):
    return ledger.db.execute("SELECT * FROM positions WHERE forecast_id=?", (forecast_id,)).fetchone()


def public_forecast(ledger, forecast):
    m = forecast["metadata"]
    position = position_for(ledger, forecast["id"])
    trade = dict(position) if position else None
    if trade:
        side = "ДА" if trade["side"] == "YES" else "НЕТ"
        paid = dec(trade["cost"]) + dec(trade["fee"])
        price = dec(trade["cost"]) / dec(trade["shares"])
        entry = f"{price:.4f}; исполнено по доступным предложениям на {iso(trade['at'])}"
        stake = f"${paid:.4f}, включая комиссию ${dec(trade['fee']):.5f}"
    else:
        side = "НАБЛЮДЕНИЕ — виртуальная позиция не открыта"
        skipped = ledger.db.execute("SELECT reason FROM skips WHERE forecast_id=? ORDER BY id DESC LIMIT 1",
                                    (forecast["id"],)).fetchone()
        reason = skipped[0] if skipped else "execution_not_completed"
        reason_ru = {
            "price_limit": "цена выше допустимой", "insufficient_edge": "недостаточный запас к цене",
            "insufficient_depth": "недостаточный объём", "position_limit": "достигнут лимит позиции",
            "event_limit": "достигнут лимит события", "total_limit": "достигнут общий лимит",
            "stale_or_nonprospective_quote": "котировка не прошла проверку времени",
            "entry_source_or_validation_failure": "данные для входа не прошли проверку",
            "below_minimum_order_size": "сумма ниже минимального размера заявки",
            "execution_not_completed": "исполнение не завершено",
        }.get(reason, "вход не прошёл проверку")
        entry = f"рынок ДА {float(forecast['baseline']):.1%}; вход пропущен: {reason_ru}"
        stake = "$0"
    return render_forecast({
        "forecast_id": f"P{forecast['id']}", "event": m["title_ru"],
        "market_url": m["url"], "side": side,
        "model_probability": f"сохранение ставки {float(forecast['probability']):.1%} (экспериментальная модель)",
        "market_price": entry, "paper_stake": stake,
        "source_as_of": m["data_asof"], "forecast_at": iso(forecast["issued_at"]),
        "expires_at": iso(m["entry_expires_at"]), "reason": m["reason_ru"],
    })


def recover_publications(ledger, box, now):
    """Rebuild missing outbox rows from immutable paper facts after a crash."""
    for forecast in ledger.list_forecasts():
        key = f"forecast:{forecast['id']}"
        if box.get(key):
            continue
        m = forecast["metadata"]
        # Publication cadence is frozen with the forecast; results never overwrite it.
        if m.get("publish"):
            box.enqueue(key, public_forecast(ledger, forecast), expires_at=m["entry_expires_at"])


def settle_and_publish(ledger, box, now, *, resolve=final_outcome):
    errors = []
    by_market = {}
    for forecast in ledger.list_forecasts():
        by_market.setdefault(forecast["market_id"], []).append(forecast)
    for market_id, forecasts in by_market.items():
        settlement = ledger.db.execute("SELECT * FROM settlements WHERE market_id=?", (market_id,)).fetchone()
        if settlement is None:
            # Never infer resolution from a quote. The oracle can settle after the event.
            try:
                outcome = resolve(forecasts[0]["metadata"]["condition_id"])
            except (DataError, ValueError) as exc:
                errors.append("settlement_manual_resolution_required" if str(exc) ==
                              "settlement_manual_resolution_required" else "settlement_source_unavailable")
                continue
            if outcome is None:
                if now > forecasts[0]["closes_at"] + 72 * 3600:
                    errors.append("settlement_confirmation_overdue")
                continue
            ledger.settle(market_id, outcome, settled_at=now, now=now)
            settlement = ledger.db.execute("SELECT * FROM settlements WHERE market_id=?", (market_id,)).fetchone()
        for forecast in forecasts:
            published = box.get(f"forecast:{forecast['id']}")
            key = f"result:{forecast['id']}"
            if not published or published["state"] != "confirmed" or box.get(key):
                continue
            p = position_for(ledger, forecast["id"])
            if p:
                outcome = settlement["outcome"]
                payout = ledger._payout(dict(p), outcome)
                cost = dec(p["cost"]) + dec(p["fee"])
                pnl = payout - cost
                result_status = "ПРИБЫЛЬ" if pnl > 0 else "УБЫТОК" if pnl < 0 else "БЕЗ ИЗМЕНЕНИЯ"
                fill = f"{p['side']}, {dec(p['shares']):.6f} долей"
                costs = f"стоимость ${dec(p['cost']):.4f}; комиссия ${dec(p['fee']):.5f}"
            else:
                pnl, fill, costs = dec(0), "позиция не открывалась", "$0"
                result_status = "ПРОГНОЗ ПРОВЕРЕН, СДЕЛКИ НЕ БЫЛО"
            m = forecast["metadata"]
            text = render_result({
                "result_id": f"R{forecast['id']}", "forecast_id": f"P{forecast['id']}",
                "event": m["title_ru"], "market_url": m["url"],
                "status": result_status, "outcome": settlement["outcome"],
                "fill_summary": fill, "costs": costs, "net_pnl": f"${pnl:+.4f}",
                "resolution_source": f"https://clob.polymarket.com/markets/{m['condition_id']}",
                "source_as_of": f"первое подтверждение источника {iso(settlement['at'])}",
                "settled_at": iso(settlement["recorded_at"]),
            })
            box.enqueue(key, text, reply_to=published["message_id"])
    return errors


def make_forecast(ledger, *, slug, meeting, now=None, budget="10", get_inputs=fetch_model_inputs,
                  market_loader=load_hold_market, book_loader=get_book):
    """One revision per UTC day; no hindsight changes to an existing revision."""
    clock = datetime.now(timezone.utc) if now is None else datetime.fromtimestamp(now, timezone.utc)
    revision = clock.date().isoformat()
    previous = [f for f in ledger.list_forecasts() if f["metadata"].get("meeting") == meeting]
    if any(f["revision"] == revision for f in previous):
        return {"state": "already_recorded", "revision": revision}
    cutoff = datetime.fromisoformat(meeting + "T14:00:00").replace(
        tzinfo=ZoneInfo("America/New_York")).timestamp()
    if cutoff - clock.timestamp() < 12 * 3600:
        return {"state": "no_forecast", "reason": "event_too_close_or_finished"}
    inputs = get_inputs(meeting)
    market = market_loader(slug, meeting)
    yes = book_loader(market["tokens_by_side"]["YES"], market["conditionId"], now=now)
    p = float(inputs["probabilities"]["hold"])
    # One half-tick propagated through monthly day weighting; this is a sensitivity
    # reserve, not a confidence interval or proof that the model is calibrated.
    decision = datetime.strptime(meeting, "%Y-%m-%d")
    days = calendar.monthrange(decision.year, decision.month)[1]
    tick_reserve = 0.00125 * days / (days - decision.day) / 0.25
    margin = 0.03 + tick_reserve
    publish = not previous or abs(p - float(previous[-1]["probability"])) >= 0.05
    issued = time.time() if now is None else now
    if abs(p - yes["midpoint"]) >= margin:
        publish = True
    metadata = {
        "meeting": meeting, "condition_id": market["conditionId"],
        "title_ru": f"Сохранит ли ФРС ставку на заседании {meeting}?",
        "url": market["url"], "inputs": inputs, "quote": yes["raw"],
        "data_asof": f"EFFR {inputs['effr']['effective_date']}; ZQ {inputs['zq']['asof']}",
        "entry_expires_at": int(issued) + 600, "publish": publish,
        "margin": margin, "fee_rate": market["fee_rate"],
        "reason_ru": (
            "Сопоставляем фьючерс на ставку и текущую эффективную ставку ФРС. "
            "Модель допускает сохранение ставки либо один шаг 25 б.п.; это проверяемая гипотеза. "
            "Сделка возможна при запасе к цене после комиссии и чувствительности к одному тику. "
            "Точность и прибыльность пока не доказаны."
        ),
    }
    fid = ledger.record_forecast(market["event_id"], market["market_id"], inputs["model"],
                                 revision, p, yes["midpoint"], issued, cutoff,
                                 metadata=metadata, now=issued)
    side = "YES" if p >= yes["midpoint"] else "NO"
    probability = p if side == "YES" else 1 - p
    cap = entry_limit(probability, market["fee_rate"], margin)
    if cap <= 0:
        trade = ledger.record_skip(fid, "insufficient_edge", now=issued)
    else:
        try:
            # A new read after recording avoids filling a quote from before the forecast.
            quote_now = time.time() if now is None else now
            book = book_loader(market["tokens_by_side"][side], market["conditionId"], now=now)
            trade = ledger.buy(fid, side, book["asks"], book["quoted_at"], cap,
                               budget, market["fee_rate"], now=time.time() if now is None else now,
                               min_shares=book["min_order_size"])
        except (DataError, ValueError):
            trade = ledger.record_skip(fid, "entry_source_or_validation_failure", now=time.time() if now is None else now)
    return {"state": "recorded", "forecast_id": fid, "trade": trade, "inputs": inputs}


def status_text(report, *, status_id, now, state):
    brier = report["brier_by_model"]
    score = "ещё нет завершённых независимых событий"
    if brier:
        score = "; ".join(f"{name}: Brier модели {float(s['model']):.4f}, рынка {float(s['baseline']):.4f}"
                          for name, s in brier.items())
    details = (
        f"Закрытых виртуальных позиций: {report['closed']}, открытых: {report['open']}; "
        f"прибыльных {report['wins']}, убыточных {report['losses']}. "
        f"Реализованный результат после комиссий: ${float(report['realised_pnl']):+.4f}. "
        f"Завершённых независимых событий: {report['independent_events']}; {score}. "
        "Открытые позиции учитываются по затратам; это не текущая рыночная стоимость."
    )
    return render_status({"status_id": status_id, "as_of": iso(now), "status": state, "details": details})


def run_cycle(state_dir, *, slug, meeting=None, send=False, announce=False, now=None):
    state_dir = Path(state_dir)
    destination = os.getenv("PYTHIA_PAPER_TELEGRAM_CHAT_ID") or "preview-only"
    if destination == "preview-only":
        # An unconfigured preview can never bind production publications to a fake chat.
        state_dir = state_dir / "preview"
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "cycle.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        clock = time.time() if now is None else now
        # A separate explicit paper destination prevents confusing operator alerts
        # with channel publications. A dry run uses an isolated preview destination.
        if send and (destination == "preview-only" or not os.getenv("TELEGRAM_BOT_TOKEN")
                     or os.getenv("PYTHIA_PAPER_TELEGRAM_ENABLED") != "1"):
            raise ValueError("Explicit channel and paper publishing opt-in required")
        ledger = PaperLedger(state_dir / "ledger.sqlite3")
        box = Outbox(state_dir / "outbox.sqlite3", destination=destination)
        result = {"started_at": iso(clock), "mode": "paper_only", "errors": []}
        try:
            if any(p["chat_id"] != destination for p in box.list_states()):
                raise ValueError("Channel changed; explicit reconciliation or a new experiment required")
            recover_publications(ledger, box, clock)
            result["errors"] += settle_and_publish(ledger, box, clock)
            if slug:
                try:
                    meeting = meeting or next_meeting(datetime.fromtimestamp(clock, timezone.utc))
                    result["forecast"] = make_forecast(ledger, slug=slug, meeting=meeting, now=now)
                except (ValueError, KeyError, TypeError) as exc:
                    result["errors"].append("forecast_inputs_unavailable_or_invalid")
                    # Fixed codes only: never serialize arbitrary request exceptions.
                    result["forecast"] = {"state": "blocked", "error_type": type(exc).__name__}
            elif not announce:
                result["errors"].append("paper_event_not_configured")
            recover_publications(ledger, box, clock)
            report = ledger.report()
            result["report"] = report
            day = datetime.fromtimestamp(clock, timezone.utc)
            # Weekly summary, or a one-off launch notice explicitly requested by CLI.
            status_key = "status:paper-start-v1" if announce else f"status:week:{day.strftime('%G-%V')}"
            if (announce or day.weekday() == 6) and not box.get(status_key):
                state = "Тестируем прогнозы и публикации; торговля только виртуальная"
                if result["errors"]:
                    state = "Прогнозы приостановлены: входные данные не прошли проверку"
                box.enqueue(status_key, status_text(report, status_id=status_key, now=clock, state=state))
            result["delivery"] = box.deliver(enabled=send)
            # Newly confirmed forecasts can now receive any already-known outcomes.
            result["errors"] += settle_and_publish(ledger, box, clock)
            if send:
                result["delivery"] += box.deliver(enabled=True)
            result["publication_states"] = dict(Counter(p["state"] for p in box.list_states()))
            result["attention"] = [{"key": p["key"], "state": p["state"], "error_code": p["error_code"]}
                                   for p in box.list_states() if p["state"] in ("failed", "uncertain", "expired")]
            result["forecasts_total"] = len(ledger.list_forecasts())
            result["state"] = "degraded" if result["errors"] or any(
                p["state"] in ("failed", "uncertain", "expired") for p in box.list_states()) else "ok"
            if send and any(p["state"] in ("queued", "sending") for p in box.list_states()):
                result["state"] = "degraded"
            digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()[:12]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            save_json(state_dir / f"run_{stamp}_{digest}.json", result)
            save_json(state_dir / "last_run.json", result)
            return result
        finally:
            box.close()
            ledger.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--event-slug", default=os.getenv("PYTHIA_PAPER_EVENT_SLUG", ""))
    parser.add_argument("--meeting", default=None)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--announce", action="store_true", help="One durable launch status, not a fabricated forecast")
    args = parser.parse_args()
    try:
        result = run_cycle(args.state_dir, slug=args.event_slug, meeting=args.meeting,
                           send=args.send, announce=args.announce)
        safe = {k: v for k, v in result.items() if k != "forecast"}
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return 0 if result["state"] == "ok" else 1
    except Exception as exc:
        print(json.dumps({"state": "blocked", "error_type": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
