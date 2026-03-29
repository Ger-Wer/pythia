#!/usr/bin/env python3
"""
Pythia Calibration Analysis
Генерирует calibration curve и сохраняет как PNG.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
OUTPUT_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))


def prediction_columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(predictions)").fetchall()
    }


def valid_filter(conn: sqlite3.Connection) -> str:
    if "is_valid" not in prediction_columns(conn):
        return ""
    return " AND is_valid = 1"


def calibration_curve(db_path: str, output_path: str):
    conn = sqlite3.connect(db_path)
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='predictions'"
    ).fetchone()
    if not table_exists:
        conn.close()
        print("Predictions table does not exist yet")
        return None

    validity_suffix = valid_filter(conn)
    rows = conn.execute(
        """
        SELECT dexter_p, naked_p, market_price, outcome
        FROM predictions
        WHERE outcome IS NOT NULL AND outcome IN (0, 1)
        AND dexter_p IS NOT NULL
        """
        + validity_suffix
    ).fetchall()
    conn.close()

    if len(rows) < 20:
        print(f"Only {len(rows)} resolved predictions — need 20+ for calibration curve")
        return None

    fig, axes = plt.subplots(1, 3, figsize=(18, 6))

    for ax, (name, idx) in zip(
        axes,
        [("Dexter", 0), ("Naked R1", 1), ("Market Price", 2)],
    ):
        preds = [r[idx] / 100.0 for r in rows]
        outcomes = [r[3] for r in rows]

        bins = np.linspace(0, 1, 11)
        bin_indices = np.digitize(preds, bins) - 1

        bin_means = []
        bin_actuals = []
        bin_counts = []

        for i in range(10):
            mask = [j for j, b in enumerate(bin_indices) if b == i]
            if len(mask) >= 2:
                bin_means.append(np.mean([preds[j] for j in mask]))
                bin_actuals.append(np.mean([outcomes[j] for j in mask]))
                bin_counts.append(len(mask))

        ax.plot([0, 1], [0, 1], "k--", alpha=0.5, label="Perfect")
        if bin_means:
            ax.scatter(
                bin_means,
                bin_actuals,
                s=[c * 30 for c in bin_counts],
                alpha=0.7,
                c="steelblue",
                edgecolors="navy",
            )

        bs = np.mean([(p - o) ** 2 for p, o in zip(preds, outcomes)])
        ax.set_title(f"{name}\nBrier: {bs:.4f}")
        ax.set_xlabel("Predicted probability")
        ax.set_ylabel("Actual frequency")
        ax.set_xlim(-0.05, 1.05)
        ax.set_ylim(-0.05, 1.05)
        ax.grid(True, alpha=0.3)

    fig.suptitle(
        f"Pythia Calibration — {len(rows)} resolved predictions ({datetime.now().strftime('%Y-%m-%d')})",
        fontsize=14,
        fontweight="bold",
    )
    plt.tight_layout()
    plt.savefig(output_path, dpi=120, bbox_inches="tight")
    print(f"Saved calibration curve to {output_path}")
    return output_path


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out = os.path.join(OUTPUT_DIR, f"calibration_{datetime.now().strftime('%Y%m%d')}.png")
    calibration_curve(DB_PATH, out)
