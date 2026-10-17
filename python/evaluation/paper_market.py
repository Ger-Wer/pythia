"""Read-only Polymarket adapter for the paper experiment. No order endpoints."""

import calendar
import json
import math
import re
from datetime import datetime, timezone

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


class DataError(ValueError):
    pass


def get_json(url, *, params=None, session=requests):
    try:
        r = session.get(url, params=params, timeout=20)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        raise DataError(f"public_data_unavailable:{type(exc).__name__}") from None


def as_list(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise DataError("invalid_array") from None
    if not isinstance(value, list):
        raise DataError("invalid_array")
    return value


def number(value):
    if isinstance(value, bool):
        raise DataError("invalid_number")
    try:
        n = float(value)
    except (ValueError, TypeError):
        raise DataError("invalid_number") from None
    if not math.isfinite(n):
        raise DataError("nonfinite_number")
    return n


def load_hold_market(slug, meeting, *, session=requests):
    """An explicit event allowlist avoids matching unrelated cumulative markets."""
    d = datetime.strptime(meeting, "%Y-%m-%d")
    events = get_json(f"{GAMMA}/events", params={"slug": slug}, session=session)
    if not isinstance(events, list) or len(events) != 1:
        raise DataError("event_not_unique")
    event = events[0]
    if event.get("slug") != slug or event.get("closed") is True:
        raise DataError("event_mismatch_or_closed")
    expected = (
        f"Will there be no change in Fed interest rates after the "
        f"{calendar.month_name[d.month]} {d.year} meeting?"
    )
    matches = [m for m in event.get("markets", []) if m.get("question") == expected]
    if len(matches) != 1:
        raise DataError("hold_market_grammar_mismatch")
    m = matches[0]
    if not all(m.get(k) is True for k in ("active", "enableOrderBook", "acceptingOrders")):
        raise DataError("market_not_tradeable")
    if m.get("closed") is not False:
        raise DataError("market_closed_or_unknown")
    outcomes = as_list(m.get("outcomes"))
    tokens = as_list(m.get("clobTokenIds"))
    labels = [str(x).upper() for x in outcomes]
    if len(labels) != 2 or set(labels) != {"YES", "NO"} or len(tokens) != 2:
        raise DataError("invalid_binary_tokens")
    if any(not str(t).isdigit() for t in tokens) or len(set(tokens)) != 2:
        raise DataError("invalid_token_ids")
    if not m.get("id") or not m.get("conditionId") or not event.get("id"):
        raise DataError("missing_market_identity")
    result = dict(m)
    result.update({
        "event_id": str(event["id"]), "market_id": str(m["id"]),
        "tokens_by_side": dict(zip(labels, map(str, tokens))),
        "url": f"https://polymarket.com/event/{slug}",
        "fee_rate": fee_rate(m),
    })
    return result


def fee_rate(market):
    if market.get("feesEnabled") is False:
        return 0.0
    fee = market.get("feeSchedule") or {}
    if market.get("feesEnabled") is not True or fee.get("exponent") != 1:
        raise DataError("unsupported_fee_schedule")
    if fee.get("takerOnly") is not True:
        raise DataError("unsupported_fee_schedule")
    rate = number(fee.get("rate"))
    if not 0 <= rate <= 1:
        raise DataError("invalid_fee_rate")
    return rate


def get_book(token, condition_id, *, now=None, max_age=60, session=requests):
    book = get_json(f"{CLOB}/book", params={"token_id": token}, session=session)
    now = datetime.now(timezone.utc).timestamp() if now is None else now
    if str(book.get("asset_id")) != str(token) or book.get("market") != condition_id:
        raise DataError("orderbook_identity_mismatch")
    stamp = number(book.get("timestamp")) / 1000
    if not 0 <= now - stamp <= max_age:
        raise DataError("stale_or_future_orderbook")
    def levels(name, reverse=False):
        result = []
        for item in book.get(name, []):
            p, qty = number(item.get("price")), number(item.get("size"))
            if not 0 < p < 1 or qty <= 0:
                raise DataError("invalid_book_level")
            result.append((p, qty))
        return sorted(result, reverse=reverse)
    asks, bids = levels("asks"), levels("bids", True)
    if not asks or not bids:
        raise DataError("empty_orderbook_side")
    if bids[0][0] >= asks[0][0]:
        raise DataError("crossed_orderbook")
    min_size = number(book.get("min_order_size"))
    if min_size <= 0:
        raise DataError("invalid_min_order_size")
    # The book timestamp is the provider's last state change. Observing that same
    # still-fresh state after the forecast is a valid prospective read, not a
    # historical fill. Keep both timestamps rather than pretending they coincide.
    return {"asks": asks, "bids": bids, "quoted_at": now, "book_asof": stamp,
            "min_order_size": min_size, "midpoint": (asks[0][0] + bids[0][0]) / 2,
            "raw": book}


def final_outcome(condition_id, *, session=requests):
    """Use the CLOB winner flag; extreme trading prices are never settlement."""
    data = get_json(f"{CLOB}/markets/{condition_id}", session=session)
    if data.get("condition_id") != condition_id:
        raise DataError("settlement_identity_mismatch")
    if data.get("closed") is not True:
        return None
    tokens = data.get("tokens") or []
    if len(tokens) != 2 or {str(t.get("outcome")).upper() for t in tokens} != {"YES", "NO"}:
        raise DataError("unsupported_settlement_tokens")
    winners = [str(t["outcome"]).upper() for t in tokens if t.get("winner") is True]
    if len(winners) == 1:
        return winners[0]
    # Cancellation/split/ambiguous outcomes need authoritative manual reconciliation.
    raise DataError("settlement_manual_resolution_required")


def entry_limit(probability, fee, margin):
    """Largest ask with positive edge after fees and an explicit model margin."""
    p, rate, reserve = map(number, (probability, fee, margin))
    if not 0 <= p <= 1 or not 0 <= rate <= 1 or not 0 <= reserve < 1:
        raise DataError("invalid_entry_parameters")
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if mid + rate * mid * (1 - mid) <= p - reserve:
            lo = mid
        else:
            hi = mid
    return round(lo, 8)
