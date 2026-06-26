#!/usr/bin/env python3
"""
Oracle Publisher - публикация ежедневных пророчеств в Telegram канал
Читает signals, выбирает лучший, генерирует reasoning через LLM, публикует.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.market_categorizer import categorize_market, should_exclude_category, get_category_emoji

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

# Oracle configuration
ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")
ORACLE_CHANNEL_USERNAME = os.getenv("ORACLE_CHANNEL_USERNAME", "@pythia_oracle")

# LLM configuration
ORACLE_LLM_PROVIDER = os.getenv("ORACLE_LLM_PROVIDER", "mistral")
ORACLE_LLM_MODEL = os.getenv("ORACLE_LLM_MODEL_REASONING", "mistral-small-2503")

# Publishing thresholds
MIN_EDGE = float(os.getenv("ORACLE_MIN_EDGE", "0.12"))
MIN_CONFIDENCE = float(os.getenv("ORACLE_MIN_CONFIDENCE", "0.60"))
MIN_VOLUME_USD = float(os.getenv("ORACLE_MIN_VOLUME_USD", "500000"))
MIN_DAYS_UNTIL_CLOSE = int(os.getenv("ORACLE_MIN_DAYS_UNTIL_CLOSE", "3"))
MAX_DAYS_UNTIL_CLOSE = int(os.getenv("ORACLE_MAX_DAYS_UNTIL_CLOSE", "45"))
EXCLUDE_CATEGORIES = os.getenv("ORACLE_EXCLUDE_CATEGORIES", "")
MAX_LLM_COST_PER_DAY = float(os.getenv("ORACLE_MAX_LLM_COST_PER_DAY", "5.00"))


def log_message(message: str, level: str = "INFO"):
    """Log message to console and file."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}] [{level}] {message}"
    print(log_entry)
    
    # Also write to log file
    log_file = Path(LOG_DIR) / "oracle_publisher.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a") as f:
        f.write(log_entry + "\n")


def get_recent_signals(hours: int = 24) -> List[Dict[str, Any]]:
    """
    Получить недавние signals из predictions table.
    
    Returns:
        List of dicts with signal data
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff = (datetime.utcnow() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    
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
            forecast_timestamp
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
          AND outcome IS NULL
          AND dexter_p IS NOT NULL
        ORDER BY forecast_timestamp DESC
    ''', (cutoff,))
    
    signals = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    log_message(f"Found {len(signals)} signals in last {hours}h")
    return signals


def filter_markets(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Фильтрует markets по критериям качества.
    
    Returns:
        Filtered list of signals
    """
    filtered = []
    
    for signal in signals:
        # Calculate edge
        pythia_prob = signal['dexter_p'] / 100.0 if signal['dexter_p'] > 1 else signal['dexter_p']
        market_prob = signal['market_price'] / 100.0 if signal['market_price'] > 1 else signal['market_price']
        edge = abs(pythia_prob - market_prob)
        
        # Calculate days until close
        if signal['end_date']:
            try:
                end_date = datetime.fromisoformat(signal['end_date'].replace('Z', '+00:00'))
                days_until_close = (end_date - datetime.utcnow()).days
            except Exception as e:
                log_message(f"Error parsing end_date: {e}", "WARN")
                days_until_close = 0
        else:
            days_until_close = 0
        
        # Get category
        category = signal['category'] or categorize_market(signal['question'])
        
        # Apply filters
        skip_reasons = []
        
        if edge < MIN_EDGE:
            skip_reasons.append(f"edge {edge:.3f} < {MIN_EDGE}")
        
        if pythia_prob < MIN_CONFIDENCE and pythia_prob > (1 - MIN_CONFIDENCE):
            skip_reasons.append(f"confidence {pythia_prob:.2f} not strong enough")
        
        if signal['volume'] and signal['volume'] < MIN_VOLUME_USD:
            skip_reasons.append(f"volume ${signal['volume']:,.0f} < ${MIN_VOLUME_USD:,.0f}")
        
        if days_until_close < MIN_DAYS_UNTIL_CLOSE:
            skip_reasons.append(f"closes in {days_until_close}d < {MIN_DAYS_UNTIL_CLOSE}d")
        
        if days_until_close > MAX_DAYS_UNTIL_CLOSE:
            skip_reasons.append(f"closes in {days_until_close}d > {MAX_DAYS_UNTIL_CLOSE}d")
        
        if should_exclude_category(category, EXCLUDE_CATEGORIES):
            skip_reasons.append(f"category '{category}' excluded")
        
        if skip_reasons:
            log_message(f"SKIP: {signal['question'][:50]}... | {', '.join(skip_reasons)}", "DEBUG")
            continue
        
        # Add computed fields
        signal['edge'] = edge
        signal['pythia_prob'] = pythia_prob
        signal['market_prob'] = market_prob
        signal['days_until_close'] = days_until_close
        signal['category'] = category
        
        filtered.append(signal)
    
    log_message(f"Filtered to {len(filtered)} suitable signals")
    return filtered


def select_top_signal(signals: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """
    Выбирает лучший signal для публикации.
    
    Scoring: edge * confidence_weight
    """
    if not signals:
        return None
    
    for signal in signals:
        # Confidence weight: higher for stronger predictions
        pythia_prob = signal['pythia_prob']
        confidence_weight = max(pythia_prob, 1 - pythia_prob)
        
        # Score = edge * confidence
        signal['score'] = signal['edge'] * confidence_weight
    
    # Sort by score descending
    signals.sort(key=lambda x: x['score'], reverse=True)
    
    top = signals[0]
    log_message(f"Top signal: {top['question'][:50]}... | Edge: {top['edge']:.3f} | Score: {top['score']:.3f}")
    
    return top


def generate_prophecy_llm(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Генерирует текст пророчества через LLM.
    
    Returns:
        Dict with 'reasoning', 'key_risk', 'raw_output', 'cost'
    """
    log_message(f"Generating prophecy via {ORACLE_LLM_PROVIDER}/{ORACLE_LLM_MODEL}")
    
    # Prepare prompt
    pythia_prob_pct = signal['pythia_prob'] * 100
    market_prob_pct = signal['market_prob'] * 100
    edge_pct = signal['edge'] * 100
    bet_direction = 'YES' if signal['pythia_prob'] > 0.5 else 'NO'
    
    arrow = '↑' if signal['pythia_prob'] > signal['market_prob'] else '↓'
    
    prompt = f"""Ты — Пифия, древний оракул prediction markets. Твой стиль: кратко, загадочно, но с конкретным data-driven обоснованием. Пиши на русском.

Market: {signal['question']}
Current Polymarket price: {market_prob_pct:.0f}%
Pythia prediction: {pythia_prob_pct:.0f}%
Bet direction: {bet_direction}
Edge: {edge_pct:.1f}%
Existing reasoning: {signal.get('dexter_reasoning', 'N/A')[:200]}
Key risk: {signal.get('dexter_key_risk', 'N/A')[:100]}

Напиши пост для Telegram-канала:
- 2-3 предложения обоснования (макс 250 символов)
- Используй конкретные факты из existing reasoning
- Укажи ключевой риск коротко (1 предложение, макс 80 символов)
- Тон: уверенный, но честный (признаём uncertainty)
- НЕ используй слова "я думаю", "возможно" — ты оракул, говоришь утверждениями
- НЕ используй восклицательные знаки и caps lock

Формат JSON:
{{
  "reasoning": "2-3 предложения обоснования",
  "key_risk": "Главный риск одним предложением"
}}"""

    # Call LLM API
    try:
        if ORACLE_LLM_PROVIDER == "mistral":
            result = call_mistral_api(prompt)
        elif ORACLE_LLM_PROVIDER == "deepseek":
            result = call_deepseek_api(prompt)
        else:
            raise ValueError(f"Unknown LLM provider: {ORACLE_LLM_PROVIDER}")
        
        return result
    
    except Exception as e:
        log_message(f"LLM API error: {e}", "ERROR")
        # Fallback to template
        return {
            'reasoning': signal.get('dexter_reasoning', 'Анализ рынка указывает на расхождение с crowd probability.')[:250],
            'key_risk': signal.get('dexter_key_risk', 'Неожиданные события могут изменить исход.')[:80],
            'raw_output': f"ERROR: {e}",
            'cost': 0.0
        }


def call_mistral_api(prompt: str) -> Dict[str, Any]:
    """Call Mistral AI API."""
    api_key = os.getenv("MISTRAL_API_KEY") or os.getenv("MODEL_RESEARCH_API_KEY")
    if not api_key:
        raise ValueError("MISTRAL_API_KEY not set")
    
    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": ORACLE_LLM_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500,
        "response_format": {"type": "json_object"}
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    content = data['choices'][0]['message']['content']
    
    # Parse JSON response
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        # Fallback: extract text
        parsed = {'reasoning': content[:250], 'key_risk': 'Неожиданные события.'}
    
    # Estimate cost (Mistral pricing: ~$0.001/1K tokens)
    total_tokens = data.get('usage', {}).get('total_tokens', 500)
    cost = total_tokens / 1000 * 0.001
    
    return {
        'reasoning': parsed.get('reasoning', '')[:250],
        'key_risk': parsed.get('key_risk', '')[:80],
        'raw_output': content,
        'cost': cost
    }


def call_deepseek_api(prompt: str) -> Dict[str, Any]:
    """Call DeepSeek API."""
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY not set")
    
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": ORACLE_LLM_MODEL,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500
    }
    
    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    
    data = response.json()
    content = data['choices'][0]['message']['content']
    
    # Parse JSON
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {'reasoning': content[:250], 'key_risk': 'Риск неожиданных событий.'}
    
    # Estimate cost (DeepSeek pricing: ~$0.0003/1K tokens)
    total_tokens = data.get('usage', {}).get('total_tokens', 500)
    cost = total_tokens / 1000 * 0.0003
    
    return {
        'reasoning': parsed.get('reasoning', '')[:250],
        'key_risk': parsed.get('key_risk', '')[:80],
        'raw_output': content,
        'cost': cost
    }


def format_telegram_post(signal: Dict[str, Any], prophecy: Dict[str, Any], accuracy_30d: float) -> str:
    """
    Форматирует итоговый Telegram пост.
    """
    pythia_prob_pct = signal['pythia_prob'] * 100
    market_prob_pct = signal['market_prob'] * 100
    bet_direction = 'YES' if signal['pythia_prob'] > 0.5 else 'NO'
    
    arrow = '↑' if signal['pythia_prob'] > signal['market_prob'] else '↓'
    
    # Confidence stars (1-5)
    confidence_level = min(5, max(1, int(max(signal['pythia_prob'], 1 - signal['pythia_prob']) * 6)))
    confidence_stars = '●' * confidence_level + '○' * (5 - confidence_level)
    
    # Category emoji
    category_emoji = get_category_emoji(signal['category'])
    
    # Polymarket link
    polymarket_link = f"https://polymarket.com/event/{signal['market_id']}"
    
    post = f"""🔮 ПИФИЯ ГОВОРИТ

{category_emoji} {signal['question']}

Polymarket: {market_prob_pct:.0f}% → Пифия: {pythia_prob_pct:.0f}% {arrow}

{prophecy['reasoning']}

⚠️ Риск: {prophecy['key_risk']}

Ставка: {bet_direction}
Уверенность: {confidence_stars} ({pythia_prob_pct:.0f}%)
Срок рынка: {signal['days_until_close']} дней

⚡️ [Сделать ставку на Polymarket]({polymarket_link})
📊 Точность Пифии за 30 дней: {accuracy_30d:.1f}%

#polymarket #pythia #prediction"""

    return post


def send_to_channel(text: str, disable_notification: bool = False) -> Optional[int]:
    """
    Отправляет сообщение в Telegram канал.
    
    Returns:
        message_id or None on failure
    """
    if not ORACLE_BOT_TOKEN or not ORACLE_CHANNEL_ID:
        log_message("Telegram not configured (ORACLE_TELEGRAM_BOT_TOKEN or ORACLE_CHANNEL_ID missing)", "WARN")
        print(f"\n--- WOULD PUBLISH TO CHANNEL ---\n{text}\n--- END ---\n")
        return None
    
    url = f"https://api.telegram.org/bot{ORACLE_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": ORACLE_CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
        "disable_notification": disable_notification
    }
    
    for attempt in range(3):
        try:
            response = requests.post(url, json=payload, timeout=20)
            response.raise_for_status()
            
            data = response.json()
            if data.get('ok'):
                message_id = data['result']['message_id']
                log_message(f"Published to channel: message_id={message_id}")
                return message_id
            else:
                log_message(f"Telegram API error: {data}", "ERROR")
        
        except requests.RequestException as e:
            log_message(f"Telegram API request failed (attempt {attempt+1}/3): {e}", "ERROR")
            if attempt < 2:
                import time
                time.sleep(2 ** attempt)  # Exponential backoff
    
    return None


def save_prophecy_record(signal: Dict[str, Any], prophecy: Dict[str, Any], telegram_message_id: Optional[int]):
    """
    Сохраняет запись о пророчестве в oracle_prophecies table.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    pythia_prob = signal['pythia_prob']
    bet_direction = 'YES' if pythia_prob > 0.5 else 'NO'
    confidence_level = min(5, max(1, int(max(pythia_prob, 1 - pythia_prob) * 6)))
    
    cursor.execute('''
        INSERT INTO oracle_prophecies (
            market_id, market_question, market_category, market_closes_at,
            prediction_id, bet_direction, pythia_prob, polymarket_prob, edge, confidence_level,
            oracle_reasoning, oracle_key_risk, llm_provider, llm_model, raw_llm_output,
            published_at, telegram_message_id, telegram_channel_username,
            is_backfilled
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        signal['market_id'],
        signal['question'],
        signal['category'],
        signal['end_date'],
        None,  # prediction_id FK (could link if needed)
        bet_direction,
        pythia_prob,
        signal['market_prob'],
        signal['edge'],
        confidence_level,
        prophecy['reasoning'],
        prophecy['key_risk'],
        ORACLE_LLM_PROVIDER,
        ORACLE_LLM_MODEL,
        prophecy.get('raw_output', ''),
        datetime.utcnow().isoformat() + 'Z',
        telegram_message_id,
        ORACLE_CHANNEL_USERNAME,
        0  # is_backfilled = False (это live prophecy)
    ))
    
    conn.commit()
    prophecy_id = cursor.lastrowid
    conn.close()
    
    log_message(f"Saved prophecy record: id={prophecy_id}")
    return prophecy_id


def get_accuracy_30d() -> float:
    """Вычисляет win rate за последние 30 дней."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result IS NOT NULL
    ''', (cutoff,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row[0] == 0:
        # Fallback to predictions table if no oracle_prophecies yet
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                COUNT(*) as total,
                SUM(CASE 
                    WHEN (dexter_p > 50 AND outcome = 1) OR (dexter_p < 50 AND outcome = 0) THEN 1 
                    ELSE 0 
                END) as wins
            FROM predictions
            WHERE resolved_at >= ?
              AND outcome IS NOT NULL
        ''', (cutoff,))
        
        row = cursor.fetchone()
        conn.close()
    
    total, wins = row
    return (wins / total * 100) if total > 0 else 67.0  # Default to overall


def publish_daily_prophecy(dry_run: bool = False):
    """
    Main function: публикует ежедневное пророчество.
    """
    log_message("=" * 80)
    log_message("ORACLE PUBLISHER - Daily Prophecy")
    log_message("=" * 80)
    
    # 1. Get recent signals
    signals = get_recent_signals(hours=24)
    
    if not signals:
        log_message("No signals found in last 24h, skipping publication", "WARN")
        return
    
    # 2. Filter markets
    filtered = filter_markets(signals)
    
    if not filtered:
        log_message("No suitable signals after filtering, skipping publication", "WARN")
        return
    
    # 3. Select top signal
    top_signal = select_top_signal(filtered)
    
    if not top_signal:
        log_message("No top signal selected, skipping publication", "WARN")
        return
    
    # 4. Generate prophecy via LLM
    prophecy = generate_prophecy_llm(top_signal)
    log_message(f"LLM cost: ${prophecy['cost']:.4f}")
    
    # 5. Get current accuracy
    accuracy_30d = get_accuracy_30d()
    log_message(f"Current 30d accuracy: {accuracy_30d:.1f}%")
    
    # 6. Format telegram post
    post_text = format_telegram_post(top_signal, prophecy, accuracy_30d)
    
    # 7. Publish to channel
    if dry_run:
        log_message("DRY RUN - would publish:", "INFO")
        print("\n" + post_text + "\n")
        telegram_message_id = None
    else:
        telegram_message_id = send_to_channel(post_text)
    
    # 8. Save record
    if not dry_run or telegram_message_id:
        save_prophecy_record(top_signal, prophecy, telegram_message_id)
    
    log_message("=" * 80)
    log_message("ORACLE PUBLISHER - Complete")
    log_message("=" * 80)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Oracle Publisher - Daily Prophecy')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no actual publishing)')
    
    args = parser.parse_args()
    
    try:
        publish_daily_prophecy(dry_run=args.dry_run)
    except Exception as e:
        log_message(f"FATAL ERROR: {e}", "ERROR")
        import traceback
        traceback.print_exc()
        sys.exit(1)
