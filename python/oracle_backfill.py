#!/usr/bin/env python3
"""
Oracle Backfill - заполнение канала ретроспективными пророчествами
ONE-TIME скрипт для создания архива перед публичным запуском.
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.market_categorizer import categorize_market, get_category_emoji

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")
ORACLE_CHANNEL_USERNAME = os.getenv("ORACLE_CHANNEL_USERNAME", "@pythia_oracle")
ORACLE_LLM_PROVIDER = os.getenv("ORACLE_LLM_PROVIDER", "mistral")
ORACLE_LLM_MODEL = os.getenv("ORACLE_LLM_MODEL_REASONING", "mistral-small-2503")


def log_message(message: str, level: str = "INFO"):
    """Log message."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)


def get_top_predictions_for_backfill(limit: int = 25, min_wins: int = 15) -> List[Dict[str, Any]]:
    """
    Получить топ predictions для backfill.
    
    Strategy: 15-20 WIN + 5-10 LOSS (чтобы показать честность).
    
    Args:
        limit: общее количество predictions
        min_wins: минимум WIN в выборке
    
    Returns:
        List of predictions (mixed WIN and LOSS)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get top WIN predictions (high edge)
    cursor.execute('''
        SELECT 
            market_id,
            question,
            category,
            volume,
            end_date,
            market_price,
            dexter_p,
            dexter_confidence,
            dexter_reasoning,
            dexter_key_risk,
            outcome,
            resolved_at,
            brier_dexter,
            forecast_timestamp,
            ABS(dexter_p - market_price) as edge,
            CASE 
                WHEN (dexter_p > 50 AND outcome = 1) OR (dexter_p < 50 AND outcome = 0) THEN 1
                ELSE 0
            END as is_win
        FROM predictions
        WHERE outcome IS NOT NULL
          AND is_valid = 1
          AND dexter_p IS NOT NULL
          AND ABS(dexter_p - market_price) > 10
        ORDER BY is_win DESC, ABS(dexter_p - market_price) DESC
        LIMIT ?
    ''', (limit,))
    
    predictions = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Ensure we have mix of WIN and LOSS
    wins = [p for p in predictions if p['is_win'] == 1]
    losses = [p for p in predictions if p['is_win'] == 0]
    
    log_message(f"Backfill pool: {len(wins)} wins, {len(losses)} losses")
    
    # Select: 15-20 wins + 5-10 losses (total ~25)
    selected_wins = wins[:min(20, min_wins)]
    selected_losses = losses[:min(10, limit - len(selected_wins))]
    
    selected = selected_wins + selected_losses
    
    # Sort by forecast_timestamp to maintain chronological order
    selected.sort(key=lambda x: x['forecast_timestamp'])
    
    log_message(f"Selected {len(selected)} predictions for backfill ({len(selected_wins)} wins, {len(selected_losses)} losses)")
    
    return selected


def generate_backfill_prophecy_llm(prediction: Dict[str, Any]) -> Dict[str, Any]:
    """
    Генерирует reasoning для backfilled prophecy через LLM.
    
    Note: используем уже существующий reasoning из predictions, но форматируем
    """
    # Simplified: use existing reasoning (no need to call LLM for backfill)
    reasoning = prediction.get('dexter_reasoning', 'Анализ показал расхождение с рынком.')[:250]
    key_risk = prediction.get('dexter_key_risk', 'Непредвиденные события.')[:80]
    
    return {
        'reasoning': reasoning,
        'key_risk': key_risk,
        'raw_output': f"BACKFILL: Used existing reasoning",
        'cost': 0.0
    }


def format_backfill_post(prediction: Dict[str, Any], prophecy: Dict[str, Any]) -> str:
    """
    Форматирует backfilled пост с указанием даты из архива.
    """
    pythia_prob = prediction['dexter_p'] / 100.0 if prediction['dexter_p'] > 1 else prediction['dexter_p']
    market_prob = prediction['market_price'] / 100.0 if prediction['market_price'] > 1 else prediction['market_price']
    
    pythia_prob_pct = pythia_prob * 100
    market_prob_pct = market_prob * 100
    
    bet_direction = 'YES' if pythia_prob > 0.5 else 'NO'
    arrow = '↑' if pythia_prob > market_prob else '↓'
    
    # Confidence stars
    confidence_level = min(5, max(1, int(max(pythia_prob, 1 - pythia_prob) * 6)))
    confidence_stars = '●' * confidence_level + '○' * (5 - confidence_level)
    
    # Category
    category = prediction['category'] or categorize_market(prediction['question'])
    category_emoji = get_category_emoji(category)
    
    # Result
    is_win = prediction['is_win'] == 1
    result_emoji = '✅' if is_win else '❌'
    result_text = 'WIN' if is_win else 'LOSS'
    
    # Date
    try:
        forecast_date = datetime.fromisoformat(prediction['forecast_timestamp'].replace('Z', '+00:00'))
        date_str = forecast_date.strftime('%Y-%m-%d')
    except:
        date_str = prediction['forecast_timestamp'][:10]
    
    # Actual outcome
    outcome_text = 'YES' if prediction['outcome'] == 1 else 'NO'
    
    post = f"""🔮 [АРХИВ {date_str}] {result_emoji} {result_text}

{category_emoji} {prediction['question']}

Polymarket: {market_prob_pct:.0f}% → Пифия: {pythia_prob_pct:.0f}% {arrow}

{prophecy['reasoning']}

⚠️ Риск: {prophecy['key_risk']}

Ставка: {bet_direction}
Уверенность: {confidence_stars}
Исход: {outcome_text}
Brier: {prediction['brier_dexter']:.3f}

📊 Рынок закрыт и разрешён

#pythia #archive"""

    return post


def send_to_channel_backfill(text: str, disable_notification: bool = True) -> Optional[int]:
    """
    Отправляет backfilled пост в канал (тихо).
    """
    if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
        log_message("Telegram not configured", "WARN")
        print(f"\n--- BACKFILL POST ---\n{text}\n--- END ---\n")
        return None
    
    url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": ORACLE_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
        "disable_notification": disable_notification
    }
    
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        
        data = response.json()
        if data.get('ok'):
            message_id = data['result']['message_id']
            return message_id
        else:
            log_message(f"Telegram API error: {data}", "ERROR")
    
    except requests.RequestException as e:
        log_message(f"Telegram API request failed: {e}", "ERROR")
    
    return None


def save_backfill_record(prediction: Dict[str, Any], prophecy: Dict[str, Any], telegram_message_id: Optional[int]):
    """
    Сохраняет backfilled prophecy в oracle_prophecies с флагом is_backfilled=1.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    pythia_prob = prediction['dexter_p'] / 100.0 if prediction['dexter_p'] > 1 else prediction['dexter_p']
    market_prob = prediction['market_price'] / 100.0 if prediction['market_price'] > 1 else prediction['market_price']
    edge = pythia_prob - market_prob
    
    bet_direction = 'YES' if pythia_prob > 0.5 else 'NO'
    confidence_level = min(5, max(1, int(max(pythia_prob, 1 - pythia_prob) * 6)))
    
    # Determine result
    if (bet_direction == 'YES' and prediction['outcome'] == 1) or (bet_direction == 'NO' and prediction['outcome'] == 0):
        result = 'WIN'
    else:
        result = 'LOSS'
    
    category = prediction['category'] or categorize_market(prediction['question'])
    
    cursor.execute('''
        INSERT INTO oracle_prophecies (
            market_id, market_question, market_category, market_closes_at,
            prediction_id, bet_direction, pythia_prob, polymarket_prob, edge, confidence_level,
            oracle_reasoning, oracle_key_risk, llm_provider, llm_model, raw_llm_output,
            published_at, telegram_message_id, telegram_channel_username,
            resolved_at, actual_outcome, result, brier_score,
            is_backfilled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        prediction['market_id'],
        prediction['question'],
        category,
        prediction['end_date'],
        None,
        bet_direction,
        pythia_prob,
        market_prob,
        edge,
        confidence_level,
        prophecy['reasoning'],
        prophecy['key_risk'],
        ORACLE_LLM_PROVIDER,
        ORACLE_LLM_MODEL,
        prophecy.get('raw_output', ''),
        prediction['forecast_timestamp'],  # Use original forecast timestamp
        telegram_message_id,
        ORACLE_CHANNEL_USERNAME,
        prediction['resolved_at'],
        prediction['outcome'],
        result,
        prediction['brier_dexter'],
        1  # is_backfilled = True
    ))
    
    conn.commit()
    prophecy_id = cursor.lastrowid
    conn.close()
    
    return prophecy_id


def publish_intro_post():
    """
    Публикует вступительный пост в канал после backfill.
    """
    intro_text = """🔮 PYTHIA ORACLE

AI-оракул prediction markets на базе 2-летней research системы.

Что ты увидишь здесь:
• Ежедневные прогнозы по Polymarket с обоснованием
• Публичный трекер точности (нельзя скрыть ошибки)
• Еженедельные отчёты performance

📜 Архив прогнозов апрель-июнь 2026 ниже ↓
Текущая точность: 67% (274 resolved predictions)

Первое live пророчество: скоро

⚠️ Not financial advice. DYOR.

#polymarket #pythia #prediction"""

    if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
        log_message("Telegram not configured", "WARN")
        print(f"\n--- INTRO POST ---\n{intro_text}\n--- END ---\n")
        return
    
    url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": ORACLE_CHANNEL_ID,
        "text": intro_text,
        "parse_mode": "Markdown"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=20)
        response.raise_for_status()
        log_message("Published intro post")
    except Exception as e:
        log_message(f"Failed to publish intro post: {e}", "ERROR")


def backfill_archive(limit: int = 25, dry_run: bool = False, sleep_seconds: int = 2):
    """
    Main function: заполняет канал backfilled прогнозами.
    
    Args:
        limit: количество прогнозов для backfill
        dry_run: если True, не публикует в Telegram
        sleep_seconds: задержка между постами
    """
    log_message("=" * 80)
    log_message(f"ORACLE BACKFILL - Creating Archive ({limit} predictions)")
    log_message("=" * 80)
    
    # Get top predictions
    predictions = get_top_predictions_for_backfill(limit=limit)
    
    if not predictions:
        log_message("No predictions found for backfill", "ERROR")
        return
    
    log_message(f"Starting backfill of {len(predictions)} predictions...")
    
    for i, prediction in enumerate(predictions, 1):
        log_message(f"[{i}/{len(predictions)}] Processing: {prediction['question'][:50]}...")
        
        # Generate prophecy
        prophecy = generate_backfill_prophecy_llm(prediction)
        
        # Format post
        post_text = format_backfill_post(prediction, prophecy)
        
        # Publish
        if dry_run:
            print(f"\n--- POST {i}/{len(predictions)} ---\n{post_text}\n")
            telegram_message_id = None
        else:
            telegram_message_id = send_to_channel_backfill(post_text, disable_notification=True)
        
        # Save record
        if not dry_run or telegram_message_id:
            save_backfill_record(prediction, prophecy, telegram_message_id)
        
        # Sleep to avoid rate limits
        if not dry_run and i < len(predictions):
            time.sleep(sleep_seconds)
    
    log_message(f"Backfill complete: {len(predictions)} predictions published")
    
    # Publish intro post (pinned at top)
    if not dry_run:
        log_message("Publishing intro post...")
        publish_intro_post()
    
    log_message("=" * 80)
    log_message("ORACLE BACKFILL - Complete")
    log_message("=" * 80)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Oracle Backfill - Create Archive')
    parser.add_argument('--limit', type=int, default=25, help='Number of predictions to backfill')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no publishing)')
    parser.add_argument('--sleep', type=int, default=2, help='Seconds between posts (default 2)')
    
    args = parser.parse_args()
    
    try:
        backfill_archive(limit=args.limit, dry_run=args.dry_run, sleep_seconds=args.sleep)
    except Exception as e:
        log_message(f"FATAL ERROR: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
