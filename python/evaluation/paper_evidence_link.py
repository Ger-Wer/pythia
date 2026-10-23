"""R7: evidence linkage for the paper cycle (docs/EVIDENCE_R7_PLAN.md).

Links each paper forecast to its input snapshot and to the evidence dossier
of its meeting, and verifies the end-to-end chain
snapshot -> forecast -> fill/skip -> post -> outcome.

The pilot ledger and outbox are opened READ-ONLY and never modified; links
live in a separate append-only store, evidence_links.sqlite3, next to them.
Evidence keeps usable_for_forecast=false: a link records provenance and
context, it never feeds a probability.
"""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

LEDGER_NAME = "ledger.sqlite3"
OUTBOX_NAME = "outbox.sqlite3"
LINKS_NAME = "evidence_links.sqlite3"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ro_connect(path):
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def snapshot_payload(metadata):
    """The snapshot of a forecast is the inputs it was computed from."""
    if not isinstance(metadata, dict):
        return None
    meeting = metadata.get("meeting")
    inputs = metadata.get("inputs")
    if not meeting or inputs is None:
        return None
    return {"meeting": meeting, "inputs": inputs, "quote": metadata.get("quote")}


def snapshot_ref(payload):
    return "sha256:" + sha256_hex(canonical(payload))


class LinkStore:
    """Append-only link store; mirrors the paper ledger's immutability."""

    def __init__(self, path):
        self.db = sqlite3.connect(path, timeout=15, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_ref TEXT PRIMARY KEY, meeting TEXT NOT NULL,
                payload TEXT NOT NULL, origins TEXT NOT NULL,
                recorded_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS forecast_links (
                forecast_id INTEGER PRIMARY KEY, snapshot_ref TEXT,
                dossier_ref TEXT, claim_ids TEXT, jev_verdict_ref TEXT,
                state TEXT NOT NULL, reason_code TEXT,
                recorded_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS chain_checks (
                id INTEGER PRIMARY KEY, forecast_id INTEGER NOT NULL,
                digest TEXT NOT NULL, chain TEXT NOT NULL,
                recorded_at INTEGER NOT NULL);
        """)
        for table in ("snapshots", "forecast_links", "chain_checks"):
            for operation in ("UPDATE", "DELETE"):
                self.db.execute(
                    f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} "
                    f"BEFORE {operation} ON {table} BEGIN "
                    "SELECT RAISE(ABORT, 'append-only evidence links'); END")

    def close(self):
        self.db.close()


def read_forecasts(state_dir):
    db = _ro_connect(Path(state_dir) / LEDGER_NAME)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM forecasts ORDER BY id").fetchall()
        forecasts = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item["metadata"])
            except (TypeError, ValueError):
                item["metadata"] = {}
            forecasts.append(item)
        return forecasts
    finally:
        db.close()


def link_all(state_dir, manifest, *, now=None):
    """Write snapshot + forecast links for every forecast in the ledger.

    Idempotent: existing rows are never rewritten (INSERT OR IGNORE).
    Returns counts. The ledger and outbox files are only read.
    """
    stamp = int(time.time() if now is None else now)
    meetings = (manifest or {}).get("meetings", {})
    store = LinkStore(Path(state_dir) / LINKS_NAME)
    counts = {"forecasts": 0, "linked": 0, "unlinked": 0, "snapshots": 0}
    try:
        store.db.execute("BEGIN IMMEDIATE")
        for forecast in read_forecasts(state_dir):
            counts["forecasts"] += 1
            metadata = forecast["metadata"]
            payload = snapshot_payload(metadata)
            ref = None
            dossier = None
            state, reason = "unlinked", None
            if payload is None:
                reason = "snapshot_unresolvable"
            else:
                ref = snapshot_ref(payload)
                origins = meetings.get(payload["meeting"], {}).get(
                    "snapshot_origins", [])
                cursor = store.db.execute(
                    "INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?)",
                    (ref, payload["meeting"], canonical(payload),
                     canonical(origins), stamp))
                counts["snapshots"] += cursor.rowcount
                dossier = meetings.get(payload["meeting"])
                if dossier is None:
                    reason = "dossier_missing"
                else:
                    state = "linked"
            store.db.execute(
                "INSERT OR IGNORE INTO forecast_links VALUES (?,?,?,?,?,?,?,?)",
                (forecast["id"], ref,
                 dossier.get("dossier_ref") if dossier else None,
                 canonical(dossier.get("claim_ids")) if dossier else None,
                 dossier.get("jev_verdict_ref") if dossier else None,
                 state, reason, stamp))
            counts["linked" if state == "linked" else "unlinked"] += 1
        store.db.execute("COMMIT")
    except BaseException:
        store.db.execute("ROLLBACK")
        raise
    finally:
        store.close()
    return counts


def build_chain(state_dir, forecast):
    """Assemble the end-to-end chain for one forecast from read-only facts."""
    state_dir = Path(state_dir)
    fid = forecast["id"]
    chain = {"forecast_id": fid, "missing": []}

    links_path = state_dir / LINKS_NAME
    link = None
    if links_path.exists():
        db = _ro_connect(links_path)
        db.row_factory = sqlite3.Row
        try:
            row = db.execute("SELECT * FROM forecast_links WHERE forecast_id=?",
                             (fid,)).fetchone()
            link = dict(row) if row else None
        finally:
            db.close()
    if link is None:
        chain["missing"].append("evidence_link")
        chain["snapshot_ref"] = None
    else:
        chain["snapshot_ref"] = link["snapshot_ref"]
        chain["evidence"] = {
            "state": link["state"], "reason_code": link["reason_code"],
            "dossier_ref": link["dossier_ref"],
            "jev_verdict_ref": link["jev_verdict_ref"],
        }
        if link["snapshot_ref"] is None:
            chain["missing"].append("snapshot")

    chain["forecast"] = {
        "event_id": forecast["event_id"], "market_id": forecast["market_id"],
        "model": forecast["model"], "revision": forecast["revision"],
        "probability": forecast["probability"],
        "baseline": forecast["baseline"], "issued_at": forecast["issued_at"],
    }

    ledger = _ro_connect(state_dir / LEDGER_NAME)
    ledger.row_factory = sqlite3.Row
    try:
        position = ledger.execute(
            "SELECT * FROM positions WHERE forecast_id=?", (fid,)).fetchone()
        skips = ledger.execute(
            "SELECT reason FROM skips WHERE forecast_id=? ORDER BY id",
            (fid,)).fetchall()
        settlement = ledger.execute(
            "SELECT * FROM settlements WHERE market_id=?",
            (forecast["market_id"],)).fetchone()
    finally:
        ledger.close()
    if position is not None:
        chain["execution"] = {"kind": "fill", "side": position["side"],
                              "shares": position["shares"],
                              "cost": position["cost"], "fee": position["fee"]}
    elif skips:
        chain["execution"] = {"kind": "skip",
                              "reasons": [row["reason"] for row in skips]}
    else:
        chain["execution"] = {"kind": "none"}
        chain["missing"].append("execution")

    outbox_path = state_dir / OUTBOX_NAME
    posts = {}
    if outbox_path.exists():
        box = _ro_connect(outbox_path)
        box.row_factory = sqlite3.Row
        try:
            for kind in ("forecast", "result"):
                row = box.execute("SELECT * FROM outbox WHERE key=?",
                                  (f"{kind}:{fid}",)).fetchone()
                posts[kind] = dict(row) if row else None
        finally:
            box.close()
    post = posts.get("forecast")
    if post is None:
        chain["post"] = None
        chain["missing"].append("post")
    else:
        chain["post"] = {"state": post["state"],
                         "message_id": post["message_id"],
                         "error_code": post["error_code"]}
    if settlement is None:
        chain["outcome"] = None
        chain["missing"].append("outcome")
    else:
        result_post = posts.get("result")
        chain["outcome"] = {
            "outcome": settlement["outcome"], "at": settlement["at"],
            "result_post": None if result_post is None else {
                "state": result_post["state"],
                "message_id": result_post["message_id"]},
        }
        if result_post is None:
            chain["missing"].append("result_post")
    chain["complete"] = not chain["missing"]
    return chain


def verify_all(state_dir):
    """Build every chain; the digest is over chain content only."""
    chains = [build_chain(state_dir, f) for f in read_forecasts(state_dir)]
    return {"chains": chains, "digest": sha256_hex(canonical(chains))}


def dry_run(state_dir, manifest, *, now=None):
    """Link + verify on a throwaway copy; the real state dir is untouched."""
    state_dir = Path(state_dir)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        for name in (LEDGER_NAME, OUTBOX_NAME):
            for suffix in ("", "-wal", "-shm"):
                source = state_dir / (name + suffix)
                if source.exists():
                    shutil.copy2(source, work / (name + suffix))
        counts = link_all(work, manifest, now=now)
        report = verify_all(work)
        report["link_counts"] = counts
        return report


def record_checks(state_dir, *, now=None):
    """Persist one chain_checks row per forecast for the current chains."""
    stamp = int(time.time() if now is None else now)
    report = verify_all(state_dir)
    store = LinkStore(Path(state_dir) / LINKS_NAME)
    try:
        store.db.execute("BEGIN IMMEDIATE")
        for chain in report["chains"]:
            store.db.execute(
                "INSERT INTO chain_checks VALUES (NULL,?,?,?,?)",
                (chain["forecast_id"], sha256_hex(canonical(chain)),
                 canonical(chain), stamp))
        store.db.execute("COMMIT")
    except BaseException:
        store.db.execute("ROLLBACK")
        raise
    finally:
        store.close()
    return report


def load_manifest(path):
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("link", "verify", "dry-run"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--state-dir", type=Path, required=True)
        cmd.add_argument("--manifest", type=Path, default=None)
    chain_cmd = sub.add_parser("chain")
    chain_cmd.add_argument("--state-dir", type=Path, required=True)
    chain_cmd.add_argument("--forecast-id", type=int, required=True)
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if args.command == "link":
        counts = link_all(args.state_dir, manifest)
        report = record_checks(args.state_dir)
        print(json.dumps({"link_counts": counts, "digest": report["digest"]},
                         ensure_ascii=False, indent=2))
    elif args.command == "verify":
        print(json.dumps(verify_all(args.state_dir),
                         ensure_ascii=False, indent=2))
    elif args.command == "dry-run":
        print(json.dumps(dry_run(args.state_dir, manifest),
                         ensure_ascii=False, indent=2))
    elif args.command == "chain":
        forecasts = {f["id"]: f for f in read_forecasts(args.state_dir)}
        if args.forecast_id not in forecasts:
            print(json.dumps({"error": "forecast_not_found"}))
            return 1
        print(json.dumps(build_chain(args.state_dir, forecasts[args.forecast_id]),
                         ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
