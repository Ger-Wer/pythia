#!/usr/bin/env python3
"""R5 Layer B measurement runner — real TypeSafe JEV edition.
Runs on amper as the pythia user (the TypeSafe key and the egress live
there). Builds claims from the frozen R4 corpus labels and scores the
gate verdicts per split. Results are appended to a JSONL as they
complete; finished (model, claim, repeat) keys are skipped on rerun.
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[4]))  # repo root, for python.evaluation imports

from python.evaluation.jev_gate import JEV_CONFIG, verify_claim_jev as verify_claim  # noqa: E402

API = JEV_CONFIG["judge_endpoint"]
MODEL = JEV_CONFIG["judge_model"]
DATA = json.loads((HERE / "jev_eval_input.json").read_text())
FRAC = {0.0: "", 0.25: "-1/4", 0.5: "-1/2", 0.75: "-3/4"}


def parse_range_num(s: str) -> float:
    s = s.replace("\u2011", "-")
    if "/" in s:
        whole, frac = s.split("-") if "-" in s else ("0", s)
        n, d = frac.split("/")
        return int(whole) + int(n) / int(d)
    return float(s)


def fmt_range_num(x: float) -> str:
    whole = int(x)
    frac = round(x - whole, 2)
    if frac == 0:
        return str(whole)
    suffix = FRAC[frac]
    return (str(whole) + suffix) if whole else suffix.lstrip("-")


def build_claims() -> list[dict]:
    claims = []
    for sid, d in DATA.items():
        lo, hi = d["range"]
        date = d["date_text"]
        month_year = " ".join(date.replace(",", "").split()[-2:])
        year = int(date.split()[-1])
        claims.append({
            "id": f"{sid}:support", "source": sid, "split": d["split"], "expected": "support",
            "text": f"In its {date} statement, the FOMC announced a target range for the federal funds rate of {lo} to {hi} percent.",
            "numbers": [lo, hi], "fragments": d["fragments"],
        })
        lo2, hi2 = fmt_range_num(parse_range_num(lo) + 0.5), fmt_range_num(parse_range_num(hi) + 0.5)
        claims.append({
            "id": f"{sid}:contradiction", "source": sid, "split": d["split"], "expected": "contradiction",
            "text": f"In its {date} statement, the FOMC announced a target range for the federal funds rate of {lo2} to {hi2} percent.",
            "numbers": [lo2, hi2], "fragments": d["fragments"],
        })
        proj = f"{parse_range_num(hi) + 1.1:.1f}"
        claims.append({
            "id": f"{sid}:insufficient", "source": sid, "split": d["split"], "expected": "insufficient",
            "text": f"In its {date} statement, the FOMC published a median projection of {proj} percent for PCE inflation in {year + 1}.",
            "numbers": [proj], "fragments": d["fragments"],
        })
        claims.append({
            "id": f"{sid}:context", "source": sid, "split": d["split"], "expected": "context_only",
            "text": f"In a public speech in {month_year}, a Federal Reserve governor described the labor market as having cooled notably since the previous meeting.",
            "numbers": [], "fragments": d["fragments"],
        })
    return claims


def make_judge(model: str):
    key = Path("/home/pythia/.config/opencode/secrets/typesafe-key").read_text(encoding="utf-8").strip()

    def judge(state: str, questions: dict):
        payload = json.dumps({"state": state, "model": model, "questions": questions}).encode()
        last = None
        for attempt in range(3):
            req = urllib.request.Request(
                API, data=payload, method="POST",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                return data["answers"], data.get("usage"), data.get("model", model)
            except Exception as exc:  # noqa: BLE001
                last = exc
                time.sleep(3 * (attempt + 1))
        raise RuntimeError(f"transport failed: {last}")

    return judge


def summarize(claims: list[dict], done: dict) -> dict:
    metrics = {}
    for split in ("tuning", "control"):
        sel = [c for c in claims if c["split"] == split]
        if not sel:
            continue
        rows = []
        for c in sel:
            reps = [done[k] for k in sorted(done) if k[0] == c["id"]]
            if reps:
                rows.append((c, reps))
        if not rows:
            continue
        fa = sum(1 for c, reps in rows if reps[0]["verdict"] == "support" and c["expected"] != "support")
        fr = sum(1 for c, reps in rows if c["expected"] == "support" and reps[0]["verdict"] != "support")
        ex = sum(1 for c, reps in rows if reps[0]["verdict"] == c["expected"])
        dec = sum(1 for c, reps in rows if reps[0]["schema_valid"] and reps[0]["verdict"] != "insufficient")
        sv = sum(1 for c, reps in rows if reps[0]["schema_valid"])
        stb = sum(1 for c, reps in rows if len(reps) > 1 and len({r["verdict"] for r in reps}) == 1)
        stb_n = sum(1 for c, reps in rows if len(reps) > 1)
        tok = sum((r["usage"] or {}).get("total_tokens", 0) for c, reps in rows for r in reps)
        calls = sum(len(reps) for c, reps in rows)
        metrics[split] = {"claims": len(rows), "false_accepts": fa, "false_rejects": fr, "exact": ex,
                          "decided": dec, "schema_valid": sv, "stable": stb, "stable_of": stb_n,
                          "tokens": tok, "judge_calls": calls}
    return metrics


def main() -> None:
    mode = sys.argv[1]  # smoke | full
    models = sys.argv[2].split(",")
    claims = build_claims()
    if mode == "smoke":
        claims = [c for c in claims if c["split"] == "tuning"]
        repeats = 1
    else:
        repeats = 3
    jsonl = HERE / f"jev_results_jev_{mode}.jsonl"
    done: dict[tuple, dict] = {}
    if jsonl.exists():
        for line in jsonl.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                if (rec["verdict"].get("reason") or "").startswith("schema_invalid: transport error"):
                    continue  # transport failures are not measurements; re-judge
                done[(rec["model"], rec["claim_id"], rec["rep"])] = rec["verdict"]
    with jsonl.open("a") as fh:
        for model in models:
            judge = make_judge(model)
            for rep in range(repeats):
                for c in claims:
                    key = (model, c["id"], rep)
                    if key in done:
                        continue
                    v = verify_claim(c["text"], c["numbers"], c["fragments"], judge)
                    d = v.to_dict()
                    fh.write(json.dumps({"model": model, "claim_id": c["id"], "rep": rep, "verdict": d}) + "\n")
                    fh.flush()
                    done[key] = d
                    time.sleep(0.5)
            per_model = {k[1:]: v for k, v in done.items() if k[0] == model}
            print(model, json.dumps(summarize(claims, per_model), indent=1), flush=True)
    claims_out = [{k: c[k] for k in ("id", "source", "split", "expected", "text", "numbers")} for c in build_claims()]
    (HERE / f"jev_claims_{mode}.json").write_text(json.dumps(claims_out, ensure_ascii=False, indent=1))
    print("done; results in", jsonl)


if __name__ == "__main__":
    main()
