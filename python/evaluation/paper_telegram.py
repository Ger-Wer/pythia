"""Pythia v2 paper-only Telegram delivery; no v1 imports or trading operations.

Construct Outbox(path, destination=explicit_channel_id), enqueue immutable posts, then
call deliver(enabled=True) only after operator approval. Both --send/explicit enabled
and PYTHIA_PAPER_TELEGRAM_ENABLED=1 are required. Credentials are read only from
TELEGRAM_BOT_TOKEN and PYTHIA_PAPER_TELEGRAM_CHAT_ID. Sessions must disable POST
retries. Uncertain delivery requires a human check of the visible forecast/result ID
and reconcile(key, observed_message_id); it is never automatically retransmitted.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager

import requests

MAX_TEXT = 4000
LEASE_SECONDS = 120


def _body(lines):
    text = "\n".join(str(line) for line in lines)
    if not text.startswith("PAPER — ") or len(text.encode("utf-16-le")) // 2 > MAX_TEXT:
        raise ValueError("paper_label_or_length_invalid")
    return text


def render_forecast(f):
    """Required fields are intentionally strict: missing facts block publication."""
    return _body([
        "PAPER — бумажная торговля, реальные деньги не используются",
        f"Прогноз: {f['forecast_id']}", f"Событие: {f['event']}",
        f"Рынок: {f['market_url']}", f"Исход: {f['side']}",
        f"Вероятность модели: {f['model_probability']}",
        f"Цена входа: {f['market_price']}", f"Учебная сумма: {f['paper_stake']}",
        f"Данные доступны на: {f['source_as_of']}",
        f"Прогноз зафиксирован: {f['forecast_at']}",
        f"Окно входа до: {f['expires_at']}", f"Обоснование: {f['reason']}",
        "Это зафиксированный прогноз. Исправления и результат публикуются отдельно.",
    ])


def render_result(r):
    """Publish every settlement, including losses, cancellations and no fills."""
    return _body([
        "PAPER — результат учебной ставки, реальные деньги не использовались",
        f"Результат: {r['result_id']} | Прогноз: {r['forecast_id']}",
        f"Событие: {r['event']}", f"Рынок: {r['market_url']}",
        f"Статус: {r['status']}", f"Исход: {r['outcome']}",
        f"Учебное исполнение: {r['fill_summary']}", f"Расходы: {r['costs']}",
        f"Итог после расходов: {r['net_pnl']}",
        f"Источник результата: {r['resolution_source']}",
        f"Результат источника на: {r['source_as_of']}",
        f"Зафиксировано: {r['settled_at']}",
    ])


def render_status(s):
    return _body([
        "PAPER — статус исследования, реальные ставки не выполняются",
        f"Сводка: {s['status_id']} | На: {s['as_of']}",
        f"Состояние: {s['status']}", f"Подробности: {s['details']}",
    ])


def _enabled(enabled, env):
    return enabled and env.get("PYTHIA_PAPER_TELEGRAM_ENABLED") == "1"


def send(session, text, reply_to=None, *, enabled=False, env=None):
    """Return safe structured receipts. Never return URL, token or exception text."""
    env = os.environ if env is None else env
    _body([text])
    if not _enabled(enabled, env):
        return {"state": "dry_run"}
    token = env.get("TELEGRAM_BOT_TOKEN")
    destination = env.get("PYTHIA_PAPER_TELEGRAM_CHAT_ID")
    if not token or not destination:
        return {"state": "blocked", "error_code": "credentials_missing"}
    payload = {"chat_id": destination, "text": text, "disable_web_page_preview": True}
    if reply_to is not None:
        payload["reply_parameters"] = {"message_id": reply_to}
    try:
        response = session.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload, timeout=(3, 10),
        )
        status = response.status_code
        try:
            data = response.json()
        except Exception:
            data = {}
        if status == 429 or (isinstance(data, dict) and data.get("error_code") == 429):
            retry = data.get("parameters", {}).get("retry_after", 60) if isinstance(data, dict) else 60
            retry = retry if isinstance(retry, int) and not isinstance(retry, bool) else 60
            return {"state": "queued", "retry_after": max(1, retry),
                    "error_code": "rate_limited", "http_status": status}
        if status >= 500:
            return {"state": "uncertain", "error_code": "server_uncertain", "http_status": status}
        if 400 <= status < 500:
            return {"state": "failed", "error_code": "telegram_rejected", "http_status": status}
        if isinstance(data, dict) and data.get("ok") is True and 200 <= status < 300:
            message_id = data.get("result", {}).get("message_id")
            if isinstance(message_id, int) and not isinstance(message_id, bool) and message_id > 0:
                return {"state": "confirmed", "message_id": message_id, "http_status": status}
        if isinstance(data, dict) and data.get("ok") is False:
            return {"state": "failed", "error_code": "telegram_rejected", "http_status": status}
        return {"state": "uncertain", "error_code": "receipt_invalid", "http_status": status}
    except Exception:
        return {"state": "uncertain", "error_code": "transport_uncertain"}


class Outbox:
    def __init__(self, path, *, destination=None, env=None, clock=time.time):
        self.env = os.environ if env is None else env
        self.destination = str(destination or self.env.get("PYTHIA_PAPER_TELEGRAM_CHAT_ID", ""))
        if not self.destination:
            raise ValueError("explicit_paper_destination_required")
        self.clock = clock
        self.owner = uuid.uuid4().hex
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS outbox (
                key TEXT PRIMARY KEY, text TEXT NOT NULL, text_sha256 TEXT NOT NULL,
                chat_id TEXT NOT NULL, reply_to INTEGER, state TEXT NOT NULL DEFAULT 'queued',
                message_id INTEGER, attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, next_attempt_at REAL NOT NULL,
                owner TEXT, lease_until REAL, error_code TEXT, expires_at REAL
            );
            CREATE TABLE IF NOT EXISTS outbox_attempts (
                id INTEGER PRIMARY KEY, key TEXT NOT NULL, started_at REAL NOT NULL,
                finished_at REAL, state TEXT NOT NULL, error_code TEXT, http_status INTEGER
            );
        """)
        self._recover()

    @contextmanager
    def _tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _recover(self):
        """Only expired leases may be recovered; never disturb a live sender."""
        now = self.clock()
        with self._tx():
            expired = self.db.execute(
                "SELECT key FROM outbox WHERE state='sending' AND lease_until<=?", (now,)
            ).fetchall()
            for row in expired:
                self.db.execute(
                    "UPDATE outbox SET state='uncertain',error_code='delivery_interrupted',"
                    "owner=NULL,lease_until=NULL WHERE key=? AND state='sending'",
                    (row["key"],),
                )
                self.db.execute(
                    "UPDATE outbox_attempts SET state='uncertain',finished_at=?,"
                    "error_code='delivery_interrupted' WHERE key=? AND finished_at IS NULL",
                    (now, row["key"]),
                )

    def enqueue(self, key, text, reply_to=None, *, expires_at=None):
        _body([text])
        if not key or (reply_to is not None and (type(reply_to) is not int or reply_to <= 0)):
            raise ValueError("outbox_identity_invalid")
        if key.startswith("forecast:") and expires_at is None:
            raise ValueError("forecast_expiry_required")
        if expires_at is not None and (type(expires_at) not in (int, float) or not math.isfinite(expires_at)):
            raise ValueError("forecast_expiry_invalid")
        with self._tx():
            old = self.db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
            if old:
                if (old["text"], old["reply_to"], old["chat_id"], old["expires_at"]) != (text, reply_to, self.destination, expires_at):
                    raise ValueError("immutable_publication_conflict")
                return key
            now = self.clock()
            self.db.execute(
                "INSERT INTO outbox(key,text,text_sha256,chat_id,reply_to,created_at,next_attempt_at,expires_at,state)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (key, text, hashlib.sha256(text.encode()).hexdigest(), self.destination, reply_to, now, now,
                 expires_at, "expired" if expires_at is not None and expires_at <= now else "queued"),
            )
        return key

    def get(self, key):
        row = self.db.execute("SELECT * FROM outbox WHERE key=?", (key,)).fetchone()
        return dict(row) if row else None

    def list_states(self, state=None):
        sql, args = "SELECT * FROM outbox", ()
        if state is not None:
            sql, args = sql + " WHERE state=?", (state,)
        return [dict(row) for row in self.db.execute(sql + " ORDER BY created_at,key", args)]

    def reconcile(self, key, message_id):
        """Operator supplies a message ID verified in the intended channel."""
        if type(message_id) is not int or message_id <= 0:
            raise ValueError("message_id_invalid")
        with self._tx():
            changed = self.db.execute(
                "UPDATE outbox SET state='confirmed',message_id=?,error_code='manual_reconciliation',"
                "owner=NULL,lease_until=NULL WHERE key=? AND state='uncertain' AND chat_id=?",
                (message_id, key, self.destination),
            ).rowcount
            if changed != 1:
                raise ValueError("uncertain_publication_required")

    def deliver(self, session=None, *, enabled=False, limit=20):
        self._recover()
        with self._tx():
            self.db.execute("UPDATE outbox SET state='expired',error_code='forecast_expired'"
                            " WHERE state='queued' AND expires_at<=?", (self.clock(),))
        if not _enabled(enabled, self.env):
            return [{"key": row["key"], "state": "dry_run"} for row in self.list_states("queued")]
        if (not self.env.get("TELEGRAM_BOT_TOKEN") or
                self.env.get("PYTHIA_PAPER_TELEGRAM_CHAT_ID") != self.destination):
            return [{"state": "blocked", "error_code": "credentials_or_destination_mismatch"}]
        receipts = []
        for _ in range(limit):
            with self._tx():
                now = self.clock()
                self.db.execute(
                    "UPDATE outbox SET state='expired',error_code='forecast_expired'"
                    " WHERE state='queued' AND expires_at<=?", (now,),
                )
                row = self.db.execute(
                    "SELECT * FROM outbox WHERE state='queued' AND chat_id=? AND next_attempt_at<=?"
                    " AND (expires_at IS NULL OR expires_at>?) ORDER BY created_at,key LIMIT 1",
                    (self.destination, now, now),
                ).fetchone()
                if row is None:
                    break
                now = self.clock()
                claimed = self.db.execute(
                    "UPDATE outbox SET state='sending',owner=?,lease_until=?,attempts=attempts+1"
                    " WHERE key=? AND state='queued'",
                    (self.owner, now + LEASE_SECONDS, row["key"]),
                ).rowcount
                if claimed != 1:
                    continue
                attempt = self.db.execute(
                    "INSERT INTO outbox_attempts(key,started_at,state) VALUES(?,?,'sending')",
                    (row["key"], now),
                ).lastrowid
            if row["expires_at"] is not None and self.clock() >= row["expires_at"]:
                receipt = {"state": "expired", "error_code": "forecast_expired"}
            else:
                receipt = send(session or requests, row["text"], row["reply_to"], enabled=True, env=self.env)
            state = receipt["state"]
            # A configuration mutation between claim and send did not reach Telegram.
            if state in ("blocked", "dry_run"):
                state = "queued"
            now = self.clock()
            with self._tx():
                changed = self.db.execute(
                    "UPDATE outbox SET state=?,message_id=?,next_attempt_at=?,error_code=?,"
                    "owner=NULL,lease_until=NULL WHERE key=? AND state='sending' AND owner=?",
                    (state, receipt.get("message_id"), now + receipt.get("retry_after", 60),
                     receipt.get("error_code"), row["key"], self.owner),
                ).rowcount
                if changed != 1:
                    raise RuntimeError("delivery_lease_lost_manual_reconciliation_required")
                self.db.execute(
                    "UPDATE outbox_attempts SET finished_at=?,state=?,error_code=?,http_status=? WHERE id=?",
                    (now, state, receipt.get("error_code"), receipt.get("http_status"), attempt),
                )
            receipts.append({"key": row["key"], **receipt})
        return receipts

    def close(self):
        self.db.close()


PaperTelegramOutbox = Outbox


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--send", action="store_true", help="Explicit send; environment opt-in also required")
    args = parser.parse_args()
    try:
        box = Outbox(args.db)
        try:
            print(json.dumps(box.deliver(enabled=args.send), ensure_ascii=False))
        finally:
            box.close()
    except Exception:
        # Do not print exception strings: HTTP errors can contain bot credentials.
        print(json.dumps({"state": "blocked", "error_code": "publisher_failed"}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
