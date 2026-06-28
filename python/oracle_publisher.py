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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from python.market_categorizer import categorize_market, should_exclude_category, get_category_emoji
from python.portfolio_manager import PortfolioManager

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "data" / "pythia.db"))
LOG_DIR = os.getenv("LOG_DIR", str(BASE_DIR / "logs"))

# Oracle configuration
ORACLE_BOT_TOKEN = os.getenv("ORACLE_TELEGRAM_BOT_TOKEN", "")
ORACLE_CHANNEL_ID = os.getenv("ORACLE_TELEGRAM_CHANNEL_ID", "")
ORACLE_CHANNEL_USERNAME = os.getenv("ORACLE_CHANNEL_USERNAME", "@pythia_oracle")

# LLM configuration — DeepSeek V4 Pro for prophecy (higher quality), Mistral for forecast (free)
ORACLE_LLM_PROVIDER = os.getenv("ORACLE_LLM_PROVIDER", "deepseek")
ORACLE_LLM_MODEL = os.getenv("ORACLE_LLM_MODEL_REASONING", "deepseek-v4-pro")

# Publishing thresholds
MIN_EDGE = float(os.getenv("ORACLE_MIN_EDGE", "0.08"))
MIN_CONFIDENCE = float(os.getenv("ORACLE_MIN_CONFIDENCE", "0.60"))
MIN_VOLUME_USD = float(os.getenv("ORACLE_MIN_VOLUME_USD", "500000"))
MIN_DAYS_UNTIL_CLOSE = int(os.getenv("ORACLE_MIN_DAYS_UNTIL_CLOSE", "3"))
MAX_DAYS_UNTIL_CLOSE = int(os.getenv("ORACLE_MAX_DAYS_UNTIL_CLOSE", "45"))
EXCLUDE_CATEGORIES = os.getenv("ORACLE_EXCLUDE_CATEGORIES", "")
MAX_LLM_COST_PER_DAY = float(os.getenv("ORACLE_MAX_LLM_COST_PER_DAY", "5.00"))
ORACLE_PICKS_PER_PUBLISH = int(os.getenv("ORACLE_PICKS_PER_PUBLISH", "5"))


def log_message(message: str, level: str = "INFO"):
    """Log message to console and file."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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
    
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        SELECT 
            market_id,
            question,
            category,
            volume,
            end_date,
            market_price,
            COALESCE(ensemble_p, dexter_p, naked_p) AS best_p,
            dexter_confidence,
            COALESCE(dexter_reasoning, naked_reasoning) AS best_reasoning,
            dexter_key_risk,
            ensemble_p,
            mistral_small_p,
            deepseek_v4_pro_p,
            llm_disagreement,
            forecast_timestamp
        FROM predictions
        WHERE forecast_timestamp >= ?
          AND is_valid = 1
          AND outcome IS NULL
          AND (ensemble_p IS NOT NULL OR dexter_p IS NOT NULL OR naked_p IS NOT NULL)
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
        # Calculate edge — best_p is always percentage (0-100), convert to decimal
        pythia_prob = signal['best_p'] / 100.0
        market_prob = signal['market_price'] / 100.0 if signal['market_price'] > 1 else signal['market_price']
        edge = abs(pythia_prob - market_prob)
        
        # Calculate days until close
        if signal['end_date']:
            try:
                end_date = datetime.fromisoformat(signal['end_date'].replace('Z', '+00:00'))
                days_until_close = (end_date - datetime.now(timezone.utc)).days
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


def select_top_signals(signals: List[Dict[str, Any]], n: int = 5) -> List[Dict[str, Any]]:
    """
    Select top-N signals for publication.
    
    Scoring: edge * confidence_weight * volume_log_weight
    """
    if not signals:
        return []
    
    for signal in signals:
        pythia_prob = signal['pythia_prob']
        confidence_weight = max(pythia_prob, 1 - pythia_prob)
        volume = signal.get('volume') or 10_000
        volume_log_weight = min(1.0, max(0.1, (volume / 1_000_000)))
        signal['score'] = signal['edge'] * confidence_weight * volume_log_weight
    
    signals.sort(key=lambda x: x['score'], reverse=True)
    top = signals[:n]
    
    log_message(f"Selected {len(top)} top signals (of {len(signals)} filtered)")
    for s in top:
        log_message(f"  - {s['question'][:50]}... | Edge: {s['edge']:.3f} | Score: {s['score']:.3f}")
    
    return top


GAMMA_API = "https://gamma-api.polymarket.com"
_slug_cache: Dict[str, str] = {}
_slug_cache_time: Optional[datetime] = None
_SLUG_CACHE_TTL = timedelta(hours=1)


def _get_all_event_slugs() -> Dict[str, str]:
    """Fetch all event slugs once, cache for 1 hour."""
    global _slug_cache, _slug_cache_time
    if _slug_cache and _slug_cache_time and (datetime.now(timezone.utc) - _slug_cache_time) < _SLUG_CACHE_TTL:
        return _slug_cache
    try:
        resp = requests.get(f"{GAMMA_API}/events", params={"active": "true", "closed": "false", "limit": 500}, timeout=15)
        if resp.ok:
            _slug_cache = {}
            for event in resp.json():
                slug = event.get("slug", "")
                for m in event.get("markets", []):
                    _slug_cache[str(m.get("id"))] = slug
            _slug_cache_time = datetime.now(timezone.utc)
            log_message(f"Cached {len(_slug_cache)} event slugs")
    except Exception as e:
        log_message(f"Slug cache fetch failed: {e}", "WARN")
    return _slug_cache


def lookup_event_slug(market_id: str) -> str:
    """Look up the event slug for a domain from cached Gamma API data."""
    return _get_all_event_slugs().get(str(market_id), "")


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
    
    prompt = f"""You are Pythia Capital, a prediction market research desk. Your style: concise, data-driven, confident but honest. Write in English.

Market: {signal['question']}
Current Polymarket price: {market_prob_pct:.0f}¢
Pythia prediction: {pythia_prob_pct:.0f}¢
Speculate direction: {bet_direction}
Edge: {edge_pct:.1f}pp
Existing reasoning: {(signal.get('best_reasoning') or 'N/A')[:200]}
Key risk: {(signal.get('dexter_key_risk') or 'N/A')[:100]}

Write a Telegram post section:
- 2-3 sentences of reasoning (max 250 chars)
- Use concrete facts from existing reasoning
- State the key risk briefly (1 sentence, max 80 chars)
- Tone: confident quant desk analyst, not mystical
- DO NOT use "I think", "maybe" — you are an analyst, speak in statements
- DO NOT use exclamation marks or caps lock

JSON format:
{{
  "reasoning": "2-3 sentences of analysis",
  "key_risk": "Main risk in one sentence"
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
            'reasoning': (signal.get('best_reasoning') or 'Market analysis indicates divergence from crowd probability.')[:250],
            'key_risk': (signal.get('dexter_key_risk') or 'Unexpected events may shift the outcome.')[:80],
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
        parsed = {'reasoning': content[:250], 'key_risk': 'Unexpected events may shift outcome.'}
    
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
        parsed = {'reasoning': content[:250], 'key_risk': 'Unexpected events.'}
    
    # DeepSeek V4 Pro pricing: ~$0.0027/1K input, $0.007/1K output (est. $0.005/1K avg)
    total_tokens = data.get('usage', {}).get('total_tokens', 500)
    cost = total_tokens / 1000 * 0.005
    
    return {
        'reasoning': parsed.get('reasoning', '')[:250],
        'key_risk': parsed.get('key_risk', '')[:80],
        'raw_output': content,
        'cost': cost
    }


def format_telegram_post(signal: Dict[str, Any], prophecy: Dict[str, Any], accuracy_30d: Optional[float]) -> str:
    """
    Format the Pythia Capital desk call post (English, quant-desk style).
    """
    pythia_prob_pct = signal['pythia_prob'] * 100
    market_prob_pct = signal['market_prob'] * 100
    edge_pp = signal['edge'] * 100
    bet_direction = 'YES' if signal['pythia_prob'] > 0.5 else 'NO'
    
    # Confidence stars (1-5)
    confidence_level = min(5, max(1, int(max(signal['pythia_prob'], 1 - signal['pythia_prob']) * 6)))
    confidence_stars = '●' * confidence_level + '○' * (5 - confidence_level)
    
    # Category emoji
    category_emoji = get_category_emoji(signal['category'])
    
    # Polymarket link
    event_slug = lookup_event_slug(signal['market_id'])
    if event_slug:
        polymarket_link = f"https://polymarket.com/event/{event_slug}"
    else:
        polymarket_link = f"https://polymarket.com/event/{signal['market_id']}"
    
    # Accuracy line — only show if we have real data
    if accuracy_30d is not None:
        accuracy_line = f"📊 30d accuracy: {accuracy_30d:.0f}%"
    else:
        accuracy_line = "📊 Track record: building..."
    
    post = f"""📊 PYTHIA CAPITAL — DESK CALL

{category_emoji} {signal['question']}

Polymarket: {market_prob_pct:.0f}¢ → Pythia: {pythia_prob_pct:.0f}¢
Edge: +{edge_pp:.0f}pp | Direction: {bet_direction}

{prophecy['reasoning']}

⚠️ Risk: {prophecy['key_risk']}

Confidence: {confidence_stars}
Closes in: {signal['days_until_close']}d | Volume: ${signal['volume']:,.0f}

⚡️ {polymarket_link}
{accuracy_line}

⚠️ Not financial advice. Prediction markets carry risk of loss.

#polymarket #pythiacapital"""

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
        datetime.now(timezone.utc).isoformat() + 'Z',
        telegram_message_id,
        ORACLE_CHANNEL_USERNAME,
        0  # is_backfilled = False (это live prophecy)
    ))
    
    conn.commit()
    prophecy_id = cursor.lastrowid
    conn.close()
    
    log_message(f"Saved prophecy record: id={prophecy_id}")
    return prophecy_id


def get_accuracy_30d() -> Optional[float]:
    """Compute win rate over last 30 days. Returns None if no data (no fabrication)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    
    cursor.execute('''
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) as wins
        FROM oracle_prophecies
        WHERE resolved_at >= ?
          AND result IS NOT NULL
    ''', (cutoff,))
    
    row = cursor.fetchone()
    total, wins = row
    
    if not total or total == 0:
        # Fallback to predictions table if no oracle_prophecies yet
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
        total, wins = row
    
    conn.close()
    
    if not total or total == 0:
        return None  # No fabrication — return None when no data
    return (wins / total * 100) if wins else 0.0


def get_published_market_ids_today() -> set:
    """Get market IDs already published today to avoid duplicates."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cursor.execute('''
        SELECT market_id FROM oracle_prophecies
        WHERE published_at >= ? AND is_backfilled = 0
    ''', (today_start,))
    ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    return ids


def publish_daily_prophecy(dry_run: bool = False):
    """
    Main function: publishes daily desk calls (multi-item).
    """
    log_message("=" * 80)
    log_message("PYTHIA CAPITAL PUBLISHER - Daily Desk Calls")
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
    
    # 3. Deduplicate against today's published
    published_today = get_published_market_ids_today()
    fresh = [s for s in filtered if s['market_id'] not in published_today]
    if len(fresh) < len(filtered):
        log_message(f"Dedup: {len(filtered) - len(fresh)} already published today, {len(fresh)} fresh")
    
    if not fresh:
        log_message("All suitable signals already published today, skipping", "WARN")
        return
    
    # 4. Select top-N signals
    top_signals = select_top_signals(fresh, n=ORACLE_PICKS_PER_PUBLISH)
    
    # 5. Get accuracy
    accuracy_30d = get_accuracy_30d()
    log_message(f"30d accuracy: {accuracy_30d}")
    
    # 6. Publish each signal as separate post
    published = 0
    for signal in top_signals:
        prophecy = generate_prophecy_llm(signal)
        log_message(f"LLM cost: ${prophecy['cost']:.4f}")
        
        post_text = format_telegram_post(signal, prophecy, accuracy_30d)
        
        if dry_run:
            log_message("DRY RUN - would publish:", "INFO")
            print("\n" + post_text + "\n")
            telegram_message_id = None
        else:
            telegram_message_id = send_to_channel(post_text)
        
        if not dry_run or telegram_message_id:
            prophecy_id = save_prophecy_record(signal, prophecy, telegram_message_id)
            published += 1
            
            # Open virtual portfolio position
            if prophecy_id and not dry_run:
                pm = PortfolioManager(DB_PATH)
                bet_direction = 'YES' if signal['pythia_prob'] > 0.5 else 'NO'
                pm.open_position(
                    prophecy_id=prophecy_id,
                    entry_price=signal['market_prob'],
                    direction=bet_direction,
                    pythia_prob=signal['pythia_prob']
                )
        
        # Rate limit: 2s between posts
        if not dry_run and signal != top_signals[-1]:
            import time
            time.sleep(2)
    
    log_message(f"Published {published}/{len(top_signals)} desk calls")
    log_message("=" * 80)
    log_message("PYTHIA CAPITAL PUBLISHER - Complete")
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
