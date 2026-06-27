#!/usr/bin/env python3
"""
Oracle Scorecard Publisher - еженедельный отчёт точности в канал
Публикуется каждое воскресенье с summary за неделю.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")


def log_message(message: str, level: str = "INFO"):
    """Log message."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    log_file = Path(LOG_DIR) / "oracle_scorecard.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")


def get_weekly_stats() -> Dict[str, Any]:
    """
    Собрать статистику за последние 7 дней.
    
    Returns:
        Dict with weekly metrics
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
            AVG(brier_score) as avg_brier,
            AVG(ABS(edge)) as avg_edge
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result IS NOT NULL
    ''', (cutoff,))
    
    row = cursor.fetchone()
    total, wins, losses, avg_brier, avg_edge = row
    
    win_rate = (wins / total * 100) if total > 0 else 0.0
    
    conn.close()
    
    return {
        'total': total or 0,
        'wins': wins or 0,
        'losses': losses or 0,
        'win_rate': win_rate,
        'avg_brier': avg_brier or 0.0,
        'avg_edge': (avg_edge or 0.0) * 100  # Convert to percentage
    }


def get_monthly_stats() -> Dict[str, Any]:
    """
    Собрать статистику за последние 30 дней.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
            AVG(brier_score) as avg_brier
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result IS NOT NULL
    ''', (cutoff,))
    
    row = cursor.fetchone()
    total, wins, avg_brier = row
    
    win_rate = (wins / total * 100) if total > 0 else 0.0
    
    conn.close()
    
    return {
        'total': total or 0,
        'wins': wins or 0,
        'win_rate': win_rate,
        'avg_brier': avg_brier or 0.0
    }


def get_best_prophecy(days: int = 7) -> Optional[Dict[str, Any]]:
    """
    Find best prophecy in period (highest edge + WIN).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            market_question,
            ABS(edge) * 100 as edge_pct,
            pythia_prob * 100 as pythia_pct,
            polymarket_prob * 100 as market_pct,
            bet_direction,
            brier_score
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result = 'WIN'
        ORDER BY ABS(edge) DESC
        LIMIT 1
    ''', (cutoff,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None


def get_worst_prophecy(days: int = 7) -> Optional[Dict[str, Any]]:
    """
    Find worst prophecy in period (LOSS with high confidence).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            market_question,
            pythia_prob * 100 as pythia_pct,
            bet_direction,
            brier_score,
            oracle_reasoning
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result = 'LOSS'
        ORDER BY brier_score DESC
        LIMIT 1
    ''', (cutoff,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return dict(row)
    return None


def get_current_streak() -> int:
    """
    Получить текущий streak.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT current_streak
        FROM oracle_metrics
        WHERE id = 1
    ''')
    
    row = cursor.fetchone()
    conn.close()
    
    return row[0] if row else 0


def format_scorecard(
    weekly_stats: Dict[str, Any],
    monthly_stats: Dict[str, Any],
    best: Optional[Dict[str, Any]],
    worst: Optional[Dict[str, Any]],
    streak: int
) -> str:
    """
    Format weekly scorecard for Telegram (English, Pythia Capital style).
    """
    week_number = datetime.now(timezone.utc).isocalendar()[1]
    
    # Format best call
    best_text = "No data yet"
    if best:
        best_market = best['market_question'][:50]
        best_text = f'"{best_market}..." → +{best["edge_pct"]:.1f}pp edge'
    
    # Format worst call
    worst_text = ""
    if worst:
        worst_market = worst['market_question'][:40]
        worst_text = f'\n❌ Miss: "{worst_market}..." — overestimated confidence'
    
    # Streak text
    streak_text = ""
    if streak >= 3:
        streak_text = f'\n🔥 Hot streak: {streak} wins in a row'
    elif streak <= -3:
        streak_text = f'\n❄️ Cold streak: {abs(streak)} misses — recalibrating'
    
    scorecard = f"""📈 PYTHIA CAPITAL — WEEKLY REPORT (Week {week_number})

7-Day Performance:
✅ Wins: {weekly_stats['wins']}/{weekly_stats['total']} ({weekly_stats['win_rate']:.0f}%)
💰 Best call: {best_text}{worst_text}{streak_text}

30-Day Track Record:
Accuracy: {monthly_stats['win_rate']:.0f}%
Avg Brier: {monthly_stats['avg_brier']:.3f} (0=perfect, 1=worst)
Total resolved: {monthly_stats['total']}

⚠️ Not financial advice. Prediction markets carry risk of loss.

#polymarket #pythiacapital #weekly"""

    return scorecard


def send_to_channel(text: str) -> Optional[int]:
    """
    Отправить scorecard в канал.
    """
    if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
        log_message("Telegram not configured", "WARN")
        print(f"\n--- WOULD PUBLISH SCORECARD ---\n{text}\n--- END ---\n")
        return None
    
    url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": ORACLE_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        
        data = response.json()
        if data.get('ok'):
            message_id = data['result']['message_id']
            log_message(f"Published scorecard: message_id={message_id}")
            return message_id
        else:
            log_message(f"Telegram API error: {data}", "ERROR")
    
    except requests.RequestException as e:
        log_message(f"Telegram API request failed: {e}", "ERROR")
    
    return None


def publish_weekly_scorecard():
    """
    Main function: публикует еженедельный scorecard.
    """
    log_message("=" * 80)
    log_message("ORACLE SCORECARD - Weekly Report")
    log_message("=" * 80)
    
    # Gather stats
    weekly_stats = get_weekly_stats()
    monthly_stats = get_monthly_stats()
    best = get_best_prophecy(days=7)
    worst = get_worst_prophecy(days=7)
    streak = get_current_streak()
    
    log_message(f"Weekly: {weekly_stats['wins']}/{weekly_stats['total']} wins ({weekly_stats['win_rate']:.1f}%)")
    log_message(f"Monthly: {monthly_stats['win_rate']:.1f}% accuracy")
    log_message(f"Streak: {streak}")
    
    # Check if we have data
    if weekly_stats['total'] == 0:
        log_message("No resolved prophecies this week, skipping scorecard", "WARN")
        return
    
    # Format scorecard
    scorecard_text = format_scorecard(weekly_stats, monthly_stats, best, worst, streak)
    
    # Publish
    send_to_channel(scorecard_text)
    
    log_message("=" * 80)
    log_message("ORACLE SCORECARD - Complete")
    log_message("=" * 80)


if __name__ == '__main__':
    try:
        publish_weekly_scorecard()
    except Exception as e:
        log_message(f"FATAL ERROR: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
