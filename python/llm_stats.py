#!/usr/bin/env python3
"""
LLM Statistics - сравнение точности DeepSeek V4 Pro vs Mistral Small
 по Brier score и win rate.

Использование:
  python python/llm_stats.py              # статистика за 30 дней
  python python/llm_stats.py --days 7     # за 7 дней
  python python/llm_stats.py --json       # JSON формат
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))


def get_llm_stats(days: int = 30) -> Dict[str, Any]:
    """
    Сравнительная статистика по LLM из predictions table.

    Brier score: чем меньше, тем лучше (0 = идеально).
    Win rate:预测направление (YES/NO) совпало с исходом.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    # --- Mistral Small ---
    cursor.execute('''
        SELECT
            COUNT(*) AS n,
            SUM(CASE
                WHEN (mistral_small_p IS NOT NULL AND outcome IS NOT NULL)
                THEN 1 ELSE 0
            END) AS resolved,
            AVG(CASE
                WHEN (mistral_small_p IS NOT NULL AND outcome IS NOT NULL)
                THEN ((mistral_small_p / 100.0) - outcome) * ((mistral_small_p / 100.0) - outcome)
            END) AS brier_mistral,
            SUM(CASE
                WHEN (mistral_small_p IS NOT NULL AND outcome IS NOT NULL)
                     AND ((mistral_small_p > 50 AND outcome = 1)
                          OR (mistral_small_p <= 50 AND outcome = 0))
                THEN 1 ELSE 0
            END) AS wins_mistral
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
    ''', (cutoff,))
    m = cursor.fetchone()

    # --- DeepSeek V4 Pro ---
    cursor.execute('''
        SELECT
            COUNT(*) AS n,
            SUM(CASE
                WHEN (deepseek_v4_pro_p IS NOT NULL AND outcome IS NOT NULL)
                THEN 1 ELSE 0
            END) AS resolved,
            AVG(CASE
                WHEN (deepseek_v4_pro_p IS NOT NULL AND outcome IS NOT NULL)
                THEN ((deepseek_v4_pro_p / 100.0) - outcome) * ((deepseek_v4_pro_p / 100.0) - outcome)
            END) AS brier_deepseek,
            SUM(CASE
                WHEN (deepseek_v4_pro_p IS NOT NULL AND outcome IS NOT NULL)
                     AND ((deepseek_v4_pro_p > 50 AND outcome = 1)
                          OR (deepseek_v4_pro_p <= 50 AND outcome = 0))
                THEN 1 ELSE 0
            END) AS wins_deepseek
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
    ''', (cutoff,))
    d = cursor.fetchone()

    # --- Market baseline ---
    cursor.execute('''
        SELECT
            AVG(CASE
                WHEN outcome IS NOT NULL
                THEN ((market_price / 100.0) - outcome) * ((market_price / 100.0) - outcome)
            END) AS brier_market,
            SUM(CASE
                WHEN outcome IS NOT NULL
                     AND ((market_price > 50 AND outcome = 1)
                          OR (market_price <= 50 AND outcome = 0))
                THEN 1 ELSE 0
            END) AS wins_market,
            SUM(CASE WHEN outcome IS NOT NULL THEN 1 ELSE 0 END) AS resolved_market
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
    ''', (cutoff,))
    mk = cursor.fetchone()

    # --- Ensemble ---
    cursor.execute('''
        SELECT
            AVG(CASE
                WHEN (ensemble_p IS NOT NULL AND outcome IS NOT NULL)
                THEN ((ensemble_p / 100.0) - outcome) * ((ensemble_p / 100.0) - outcome)
            END) AS brier_ensemble,
            SUM(CASE
                WHEN (ensemble_p IS NOT NULL AND outcome IS NOT NULL)
                     AND ((ensemble_p > 50 AND outcome = 1)
                          OR (ensemble_p <= 50 AND outcome = 0))
                THEN 1 ELSE 0
            END) AS wins_ensemble,
            SUM(CASE WHEN (ensemble_p IS NOT NULL AND outcome IS NOT NULL) THEN 1 ELSE 0 END) AS resolved_ensemble
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
    ''', (cutoff,))
    ens = cursor.fetchone()

    # --- Cost ---
    cursor.execute('''
        SELECT
            COALESCE(SUM(api_cost_usd), 0) AS total_cost
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
    ''', (cutoff,))
    cost_row = cursor.fetchone()

    conn.close()

    def safe(val, default=0.0):
        return val if val is not None else default

    mistral_resolved = safe(m['resolved'])
    deepseek_resolved = safe(d['resolved'])
    market_resolved = safe(mk['resolved_market'])
    ensemble_resolved = safe(ens['resolved_ensemble'])

    return {
        'days': days,
        'cutoff': cutoff,
        'total_predictions': safe(m['n']),
        'mistral': {
            'resolved': mistral_resolved,
            'brier': round(safe(m['brier_mistral']), 4) if mistral_resolved > 0 else None,
            'wins': safe(m['wins_mistral']),
            'win_rate': round(safe(m['wins_mistral']) / mistral_resolved * 100, 1) if mistral_resolved > 0 else None,
        },
        'deepseek': {
            'resolved': deepseek_resolved,
            'brier': round(safe(d['brier_deepseek']), 4) if deepseek_resolved > 0 else None,
            'wins': safe(d['wins_deepseek']),
            'win_rate': round(safe(d['wins_deepseek']) / deepseek_resolved * 100, 1) if deepseek_resolved > 0 else None,
        },
        'ensemble': {
            'resolved': ensemble_resolved,
            'brier': round(safe(ens['brier_ensemble']), 4) if ensemble_resolved > 0 else None,
            'wins': safe(ens['wins_ensemble']),
            'win_rate': round(safe(ens['wins_ensemble']) / ensemble_resolved * 100, 1) if ensemble_resolved > 0 else None,
        },
        'market': {
            'resolved': market_resolved,
            'brier': round(safe(mk['brier_market']), 4) if market_resolved > 0 else None,
            'wins': safe(mk['wins_market']),
            'win_rate': round(safe(mk['wins_market']) / market_resolved * 100, 1) if market_resolved > 0 else None,
        },
        'total_cost_usd': round(safe(cost_row['total_cost']), 4),
    }


def format_telegram(stats: Dict[str, Any]) -> str:
    """Форматирует статистику для Telegram сообщения."""
    days = stats['days']

    def fmt_brier(b):
        if b is None:
            return '—'
        return f'{b:.4f}'

    def fmt_rate(r):
        if r is None:
            return '—'
        return f'{r:.1f}%'

    lines = [
        f'📊 *LLM Comparison — {days}d*',
        '',
        '| Model | Resolved | Brier | Win Rate |',
        '|-------|----------|-------|----------|',
        f'| Mistral Small | {stats["mistral"]["resolved"]} | {fmt_brier(stats["mistral"]["brier"])} | {fmt_rate(stats["mistral"]["win_rate"])} |',
        f'| DeepSeek V4 Pro | {stats["deepseek"]["resolved"]} | {fmt_brier(stats["deepseek"]["brier"])} | {fmt_rate(stats["deepseek"]["win_rate"])} |',
        f'| Ensemble | {stats["ensemble"]["resolved"]} | {fmt_brier(stats["ensemble"]["brier"])} | {fmt_rate(stats["ensemble"]["win_rate"])} |',
        f'| Market (baseline) | {stats["market"]["resolved"]} | {fmt_brier(stats["market"]["brier"])} | {fmt_rate(stats["market"]["win_rate"])} |',
        '',
        f'💰 Total API cost: ${stats["total_cost_usd"]:.4f}',
        '',
        '_Brier: 0.0 = perfect, 0.25 = coin flip. Lower is better._',
    ]

    # Winner
    m_b = stats['mistral']['brier']
    d_b = stats['deepseek']['brier']
    if m_b is not None and d_b is not None:
        if m_b < d_b:
            diff = d_b - m_b
            lines.insert(1, f'🏆 *Mistral* leads by {diff:.4f} Brier')
        elif d_b < m_b:
            diff = m_b - d_b
            lines.insert(1, f'🏆 *DeepSeek V4 Pro* leads by {diff:.4f} Brier')
        else:
            lines.insert(1, '🤝 Tied on Brier score')

    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description='LLM Statistics')
    parser.add_argument('--days', type=int, default=30, help='Lookback period in days')
    parser.add_argument('--json', action='store_true', help='Output JSON')
    args = parser.parse_args()

    stats = get_llm_stats(days=args.days)

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print(format_telegram(stats))


if __name__ == '__main__':
    main()
