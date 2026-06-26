#!/usr/bin/env python3
"""
Oracle Score Updater - автоматическое обновление результатов закрытых рынков
Проверяет resolution и обновляет WIN/LOSS статус + метрики + редактирует Telegram посты.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.market_categorizer import get_category_emoji

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))


def log_message(message: str, level: str = "INFO"):
    """Log message to console and file."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    log_file = Path(LOG_DIR) / "oracle_score_updater.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")


def get_pending_prophecies() -> List[Dict[str, Any]]:
    """
    Получить все pending prophecies где рынок уже закрылся.
    
    Returns:
        List of prophecies waiting for resolution
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    now = datetime.utcnow().isoformat() + 'Z'
    
    cursor.execute('''
        SELECT 
            id,
            market_id,
            market_question,
            bet_direction,
            pythia_prob,
            polymarket_prob,
            edge,
            published_at,
            market_closes_at
        FROM oracle_prophecies
        WHERE resolved_at IS NULL
          AND market_closes_at < ?
        ORDER BY published_at ASC
    ''', (now,))
    
    prophecies = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    log_message(f"Found {len(prophecies)} pending prophecies past close date")
    return prophecies


def get_market_outcome(market_id: str) -> Optional[int]:
    """
    Получить outcome рынка из predictions table.
    
    Returns:
        0 (NO), 1 (YES), or None if not resolved yet
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT outcome, resolved_at
        FROM predictions
        WHERE market_id = ?
          AND outcome IS NOT NULL
        ORDER BY resolved_at DESC
        LIMIT 1
    ''', (market_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return row[0]
    return None


def update_prophecy_result(prophecy_id: int, outcome: int, resolved_at: str):
    """
    Обновить результат пророчества.
    
    Args:
        prophecy_id: ID записи в oracle_prophecies
        outcome: 0 (NO) or 1 (YES)
        resolved_at: ISO timestamp
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Get prophecy details
    cursor.execute('''
        SELECT bet_direction, pythia_prob
        FROM oracle_prophecies
        WHERE id = ?
    ''', (prophecy_id,))
    
    row = cursor.fetchone()
    if not row:
        log_message(f"Prophecy {prophecy_id} not found", "ERROR")
        conn.close()
        return
    
    bet_direction, pythia_prob = row
    
    # Determine WIN/LOSS
    if (bet_direction == 'YES' and outcome == 1) or (bet_direction == 'NO' and outcome == 0):
        result = 'WIN'
    else:
        result = 'LOSS'
    
    # Calculate Brier score
    brier_score = (pythia_prob - outcome) ** 2
    
    # Update record
    cursor.execute('''
        UPDATE oracle_prophecies
        SET resolved_at = ?,
            actual_outcome = ?,
            result = ?,
            brier_score = ?
        WHERE id = ?
    ''', (resolved_at, outcome, result, brier_score, prophecy_id))
    
    conn.commit()
    conn.close()
    
    log_message(f"Updated prophecy {prophecy_id}: {result} | Brier: {brier_score:.3f}")
    
    return result, brier_score


def edit_telegram_post(prophecy_id: int):
    """
    Редактировать Telegram пост после resolution рынка.
    Добавляет результат (WIN/LOSS), actual outcome и Brier score.
    """
    ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN")
    ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID")
    
    if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
        log_message("Telegram not configured, skipping post edit", "WARN")
        return
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT 
            telegram_message_id,
            market_question,
            market_category,
            pythia_prob,
            market_prob,
            bet_direction,
            confidence_level,
            reasoning,
            key_risk,
            actual_outcome,
            result,
            brier_score,
            published_at
        FROM oracle_prophecies
        WHERE id = ?
    ''', (prophecy_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if not row or not row['telegram_message_id']:
        log_message(f"No Telegram message found for prophecy {prophecy_id}", "WARN")
        return
    
    # Format updated post
    pythia_prob_pct = row['pythia_prob'] * 100
    market_prob_pct = row['market_prob'] * 100
    arrow = '↑' if pythia_prob_pct > market_prob_pct else '↓'
    
    category_emoji = get_category_emoji(row['market_category'] or 'other')
    confidence_stars = '●' * row['confidence_level'] + '○' * (5 - row['confidence_level'])
    
    result_emoji = '✅' if row['result'] == 'WIN' else '❌'
    result_text = 'WIN' if row['result'] == 'WIN' else 'LOSS'
    outcome_text = 'YES' if row['actual_outcome'] == 1 else 'NO'
    
    # Parse published_at date
    try:
        pub_date = datetime.fromisoformat(row['published_at'].replace('Z', '+00:00'))
        date_str = pub_date.strftime('%Y-%m-%d')
    except:
        date_str = row['published_at'][:10]
    
    updated_text = f"""🔮 [РАЗРЕШЕНО {date_str}] {result_emoji} {result_text}

{category_emoji} {row['market_question']}

Polymarket: {market_prob_pct:.0f}% → Пифия: {pythia_prob_pct:.0f}% {arrow}

{row['reasoning']}

⚠️ Риск: {row['key_risk']}

Ставка: {row['bet_direction']}
Уверенность: {confidence_stars}
Исход: {outcome_text}
Brier: {row['brier_score']:.3f}

📊 Рынок закрыт и разрешён

#pythia"""
    
    # Edit message via Telegram API
    url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/editMessageText"
    
    payload = {
        "chat_id": ORACLE_CHANNEL_ID,
        "message_id": row['telegram_message_id'],
        "text": updated_text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        
        if response.json().get('ok'):
            log_message(f"Edited Telegram post {row['telegram_message_id']} for prophecy {prophecy_id}")
        else:
            log_message(f"Failed to edit Telegram post: {response.json()}", "ERROR")
    
    except requests.RequestException as e:
        log_message(f"Telegram API error: {e}", "ERROR")


def calculate_accuracy(days: Optional[int] = None) -> Dict[str, float]:
    """
    Вычислить accuracy metrics за период.
    
    Args:
        days: период (None = all time)
    
    Returns:
        Dict with 'win_rate', 'avg_brier', 'total', 'wins', 'losses'
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    if days:
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
                AVG(brier_score) as avg_brier
            FROM oracle_prophecies
            WHERE resolved_at >= ?
              AND result IS NOT NULL
        ''', (cutoff,))
    else:
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result = 'LOSS' THEN 1 ELSE 0 END) as losses,
                AVG(brier_score) as avg_brier
            FROM oracle_prophecies
            WHERE result IS NOT NULL
        ''')
    
    row = cursor.fetchone()
    conn.close()
    
    total, wins, losses, avg_brier = row
    
    if total == 0:
        return {'win_rate': 0.0, 'avg_brier': 0.0, 'total': 0, 'wins': 0, 'losses': 0}
    
    return {
        'win_rate': (wins / total) * 100,
        'avg_brier': avg_brier or 0.0,
        'total': total,
        'wins': wins,
        'losses': losses
    }


def calculate_current_streak() -> int:
    """
    Вычислить текущий streak (последовательные WIN/LOSS).
    
    Returns:
        Positive = win streak, Negative = loss streak, 0 = no streak
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT result
        FROM oracle_prophecies
        WHERE result IS NOT NULL
        ORDER BY resolved_at DESC
        LIMIT 20
    ''')
    
    results = [row[0] for row in cursor.fetchall()]
    conn.close()
    
    if not results:
        return 0
    
    streak = 0
    current_result = results[0]
    
    for result in results:
        if result == current_result:
            streak += 1 if current_result == 'WIN' else -1
        else:
            break
    
    return streak


def update_oracle_metrics():
    """
    Обновить сводную таблицу oracle_metrics.
    """
    # Calculate metrics
    metrics_7d = calculate_accuracy(days=7)
    metrics_30d = calculate_accuracy(days=30)
    metrics_all = calculate_accuracy(days=None)
    streak = calculate_current_streak()
    
    # Calculate max win streak (all time)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT result
        FROM oracle_prophecies
        WHERE result IS NOT NULL
        ORDER BY resolved_at ASC
    ''')
    
    results = [row[0] for row in cursor.fetchall()]
    
    max_win_streak = 0
    current_win_streak = 0
    
    for result in results:
        if result == 'WIN':
            current_win_streak += 1
            max_win_streak = max(max_win_streak, current_win_streak)
        else:
            current_win_streak = 0
    
    # Calculate avg edge
    cursor.execute('''
        SELECT AVG(ABS(edge))
        FROM oracle_prophecies
        WHERE result IS NOT NULL
    ''')
    
    avg_edge = cursor.fetchone()[0] or 0.0
    
    # Update metrics table
    cursor.execute('''
        UPDATE oracle_metrics
        SET accuracy_7d = ?,
            accuracy_30d = ?,
            accuracy_all_time = ?,
            total_prophecies = ?,
            total_resolved = ?,
            total_wins = ?,
            total_losses = ?,
            current_streak = ?,
            max_win_streak = ?,
            avg_brier_7d = ?,
            avg_brier_30d = ?,
            avg_brier_all_time = ?,
            avg_edge_all_time = ?,
            updated_at = ?
        WHERE id = 1
    ''', (
        metrics_7d['win_rate'],
        metrics_30d['win_rate'],
        metrics_all['win_rate'],
        metrics_all['total'] + len(get_pending_prophecies()),  # total includes pending
        metrics_all['total'],
        metrics_all['wins'],
        metrics_all['losses'],
        streak,
        max_win_streak,
        metrics_7d['avg_brier'],
        metrics_30d['avg_brier'],
        metrics_all['avg_brier'],
        avg_edge,
        datetime.utcnow().isoformat() + 'Z'
    ))
    
    conn.commit()
    conn.close()
    
    log_message(f"Updated metrics: 7d={metrics_7d['win_rate']:.1f}% | 30d={metrics_30d['win_rate']:.1f}% | all={metrics_all['win_rate']:.1f}% | streak={streak}")


def update_oracle_scores():
    """
    Main function: обновляет все pending prophecies.
    """
    log_message("=" * 80)
    log_message("ORACLE SCORE UPDATER - Start")
    log_message("=" * 80)
    
    # Get pending prophecies
    pending = get_pending_prophecies()
    
    if not pending:
        log_message("No pending prophecies to update")
        log_message("=" * 80)
        return
    
    updated_count = 0
    
    for prophecy in pending:
        # Check if market has been resolved
        outcome = get_market_outcome(prophecy['market_id'])
        
        if outcome is None:
            log_message(f"Market {prophecy['market_id']} not resolved yet: {prophecy['market_question'][:50]}...", "DEBUG")
            continue
        
        # Update prophecy result in DB
        resolved_at = datetime.utcnow().isoformat() + 'Z'
        update_prophecy_result(prophecy['id'], outcome, resolved_at)
        
        # Edit Telegram post with result
        edit_telegram_post(prophecy['id'])
        
        updated_count += 1
    
    log_message(f"Updated {updated_count} prophecies")
    
    # Recalculate metrics
    if updated_count > 0:
        update_oracle_metrics()
    
    log_message("=" * 80)
    log_message("ORACLE SCORE UPDATER - Complete")
    log_message("=" * 80)


if __name__ == '__main__':
    try:
        update_oracle_scores()
    except Exception as e:
        log_message(f"FATAL ERROR: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
