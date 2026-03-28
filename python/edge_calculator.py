#!/usr/bin/env python3
"""
Pythia Edge Calculator
Считает Brier score и статистическую значимость edge.
Запуск: python3 edge_calculator.py [--min-samples 50]
"""

import argparse
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
from scipy import stats

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))


def brier_score(predictions: list, outcomes: list) -> float:
    """Lower is better. Perfect = 0, worst = 1, random = 0.25"""
    return float(np.mean([(p / 100.0 - o) ** 2 for p, o in zip(predictions, outcomes)]))


def safe_p_value(value: float) -> float:
    return float(value) if np.isfinite(value) else 1.0


def check_edge(db_path: str, min_samples: int = 50) -> dict:
    conn = sqlite3.connect(db_path)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not table_exists:
        conn.close()
        return {
            "status": "INSUFFICIENT_DATA",
            "n": 0,
            "needed": min_samples,
            "message": "Predictions table does not exist yet",
        }

    rows = conn.execute(
        """
        SELECT dexter_p, naked_p, market_price, outcome
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome IN (0, 1)
        AND dexter_p IS NOT NULL AND naked_p IS NOT NULL
        """
    ).fetchall()

    n = len(rows)

    if n < min_samples:
        conn.close()
        return {
            "status": "INSUFFICIENT_DATA",
            "n": n,
            "needed": min_samples,
            "message": f"Need {min_samples - n} more resolved markets",
        }

    dexter_p, naked_p, market_p, outcomes = zip(*rows)

    bs_dexter = brier_score(list(dexter_p), list(outcomes))
    bs_naked = brier_score(list(naked_p), list(outcomes))
    bs_market = brier_score(list(market_p), list(outcomes))

    diffs_dexter = [
        (d / 100.0 - o) ** 2 - (m / 100.0 - o) ** 2
        for d, m, o in zip(dexter_p, market_p, outcomes)
    ]
    _, p_value_d = stats.ttest_1samp(diffs_dexter, 0)

    diffs_naked = [
        (nk / 100.0 - o) ** 2 - (m / 100.0 - o) ** 2
        for nk, m, o in zip(naked_p, market_p, outcomes)
    ]
    _, p_value_n = stats.ttest_1samp(diffs_naked, 0)

    diffs_dn = [
        (d / 100.0 - o) ** 2 - (nk / 100.0 - o) ** 2
        for d, nk, o in zip(dexter_p, naked_p, outcomes)
    ]
    _, p_value_dn = stats.ttest_1samp(diffs_dn, 0)

    p_value_d = safe_p_value(p_value_d)
    p_value_n = safe_p_value(p_value_n)
    p_value_dn = safe_p_value(p_value_dn)

    edge_dexter = bs_market - bs_dexter
    edge_naked = bs_market - bs_naked

    best_system = "dexter" if bs_dexter < bs_naked else "naked"
    best_edge = max(edge_dexter, edge_naked)
    best_p = p_value_d if best_system == "dexter" else p_value_n

    if best_edge > 0.02 and best_p < 0.05:
        decision = "GO"
    elif best_edge < -0.02:
        decision = "STOP"
    else:
        decision = "PIVOT"

    result = {
        "status": "COMPLETE",
        "n": n,
        "brier_dexter": round(bs_dexter, 4),
        "brier_naked": round(bs_naked, 4),
        "brier_market": round(bs_market, 4),
        "edge_dexter_vs_market": round(edge_dexter, 4),
        "edge_naked_vs_market": round(edge_naked, 4),
        "p_value_dexter": round(p_value_d, 4),
        "p_value_naked": round(p_value_n, 4),
        "dexter_vs_naked": {
            "edge": round(bs_naked - bs_dexter, 4),
            "p_value": round(p_value_dn, 4),
            "dexter_adds_value": bs_dexter < bs_naked - 0.01,
        },
        "best_system": best_system,
        "decision": decision,
        "interpretation": {
            "GO": "Edge is statistically significant. Proceed to Phase 2.",
            "PIVOT": "No clear edge. Iterate prompts, expand market filters.",
            "STOP": "System performs worse than market price. Approach does not work.",
        }[decision],
    }

    conn.close()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-samples", type=int, default=50)
    args = parser.parse_args()

    result = check_edge(DB_PATH, args.min_samples)
    print(json.dumps(result, indent=2))
