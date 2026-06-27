# Oracle Public Channel - Technical Documentation

**Created:** 2026-06-26  
**Feature Branch:** `feature/oracle-public-channel`  
**Status:** Development (Pre-Launch)

## Overview

Трансформация Pythia из приватного decision desk в публичный Telegram Oracle-канал для prediction markets. Система публикует ежедневные AI-generated прогнозы с полной прозрачностью и immutable audit trail.

## Architecture

### Design Principle: Additive Only

**НЕ изменяем:**
- Existing Slow Loop (forecast, decision_core, watchlist)
- Existing `telegram_bot.py` (приватный operator inbox)
- Existing database tables (только READ)
- Production workflows

**Добавляем новый публичный слой:**

```
┌─────────────────────────────────────────────┐
│     EXISTING SLOW LOOP (Unchanged)          │
│  forecast → predictions table               │
└──────────────────┬──────────────────────────┘
                   │ READ ONLY
                   ↓
┌─────────────────────────────────────────────┐
│     NEW ORACLE PUBLIC LAYER                 │
│                                             │
│  oracle_publisher.py                        │
│    ↓                                        │
│  oracle_prophecies table                    │
│    ↓                                        │
│  Telegram PUBLIC channel                    │
│                                             │
│  oracle_score_updater.py                    │
│    ↓                                        │
│  oracle_metrics table                       │
│                                             │
│  oracle_scorecard.py                        │
│    ↓                                        │
│  Weekly reports                             │
└─────────────────────────────────────────────┘
```

## Components

### 1. Database Schema

**File:** `sql/migrations/0011_oracle_prophecies.sql`

**Tables:**
- `oracle_prophecies` - хранит все публичные прогнозы
- `oracle_metrics` - сводная таблица с текущими метриками (singleton)

**Key Features:**
- Immutable audit trail (telegram_message_id proof)
- Multi-LLM support (llm_provider, llm_model fields)
- Backfill transparency (is_backfilled flag)
- Resolution tracking (result: WIN/LOSS/PUSH)

**Apply migration:**
```bash
sqlite3 data/pythia.db < sql/migrations/0011_oracle_prophecies.sql
```

### 2. Market Categorizer

**File:** `python/market_categorizer.py`

**Purpose:** Автоматическая категоризация рынков по question text.

**Categories:**
- `politics` - выборы, референдумы, legislation
- `crypto` - Bitcoin, Ethereum, DeFi
- `sports` - NBA, Premier League, Olympics
- `pop_culture` - Oscars, music, box office
- `business` - IPO, earnings, tech companies
- `other` - всё остальное

**Usage:**
```python
from python.market_categorizer import categorize_market, get_category_emoji

category = categorize_market("Will Bitcoin exceed $120k by July?")
# Returns: 'crypto'

emoji = get_category_emoji(category)
# Returns: '₿'
```

### 3. Oracle Publisher

**File:** `python/oracle_publisher.py`

**Purpose:** Ежедневная публикация топового пророчества в Telegram канал.

**Workflow:**
1. Читает signals из `predictions` table (last 24h)
2. Фильтрует по критериям качества:
   - Min edge: 12%
   - Min confidence: 60%
   - Min volume: $500k
   - Days until close: 3-45 days
   - Excluded categories (configurable)
3. Выбирает топ-1 по score (edge × confidence)
4. Генерирует reasoning через LLM (Mistral или DeepSeek)
5. Форматирует Telegram пост
6. Публикует в канал
7. Сохраняет в `oracle_prophecies`

**Usage:**
```bash
# Dry run (no actual publishing)
python python/oracle_publisher.py --dry-run

# Live publishing
python python/oracle_publisher.py
```

**Cron Schedule:** Daily at 06:30 UTC (30 min after forecast)

**Configuration (env vars):**
```bash
ORACLE_TELEGRAM_BOT_TOKEN=<bot_token>
ORACLE_TELEGRAM_CHANNEL_ID=<channel_id>
ORACLE_LLM_PROVIDER=mistral  # or deepseek
ORACLE_LLM_MODEL_REASONING=mistral-small-2503
ORACLE_MIN_EDGE=0.12
ORACLE_MIN_CONFIDENCE=0.60
ORACLE_MIN_VOLUME_USD=500000
ORACLE_EXCLUDE_CATEGORIES=sports
```

### 4. Oracle Score Updater

**File:** `python/oracle_score_updater.py`

**Purpose:** Автоматическое обновление результатов закрытых рынков.

**Workflow:**
1. Находит pending prophecies где market_closes_at < now
2. Проверяет resolution в `predictions` table
3. Обновляет `result` (WIN/LOSS) и `brier_score`
4. Пересчитывает metrics (7d, 30d, all-time accuracy)
5. Обновляет `oracle_metrics` table

**Usage:**
```bash
python python/oracle_score_updater.py
```

**Cron Schedule:** Daily at 19:00 UTC (после resolution checker)

### 5. Oracle Scorecard

**File:** `python/oracle_scorecard.py`

**Purpose:** Еженедельный отчёт точности в канал.

**Workflow:**
1. Собирает stats за последние 7 дней
2. Находит best prophecy (highest edge + WIN)
3. Находит worst prophecy (LOSS с high confidence)
4. Получает current streak
5. Форматирует scorecard
6. Публикует в канал

**Usage:**
```bash
python python/oracle_scorecard.py
```

**Cron Schedule:** Sunday at 10:00 UTC

**Format:**
```
📜 СВИТОК ПИФИИ — неделя 26

За последние 7 дней:
✅ Верных пророчеств: 5/7 (71%)
💰 Лучшее: "BTC prediction" → +28% edge
❌ Ошибка: "Premier League" — переоценили

🔥 Текущий стрик: 4 верных подряд

📊 Статистика за 30 дней:
Точность: 68%
Средний Brier: 0.198
Всего пророчеств: 42
```

### 6. Oracle Backfill

**File:** `python/oracle_backfill.py`

**Purpose:** ONE-TIME заполнение канала ретроспективными прогнозами.

**Strategy:**
- Выбирает 15-20 WIN + 5-10 LOSS из исторических данных
- Показывает честность (не скрываем ошибки)
- Маркирует `is_backfilled=1` для прозрачности
- Публикует с prefix `[АРХИВ YYYY-MM-DD]`

**Usage:**
```bash
# Dry run (preview posts)
python python/oracle_backfill.py --limit 25 --dry-run

# Live backfill (with 2s delay between posts)
python python/oracle_backfill.py --limit 25 --sleep 2
```

**IMPORTANT:** Запускать ОДИН РАЗ перед публичным открытием канала.

## Setup Instructions

### 1. Create Telegram Bot & Channel

```bash
# 1. Create bot via @BotFather
# 2. Get bot token
# 3. Create public channel
# 4. Add bot as admin to channel
# 5. Get channel ID (can use @userinfobot)
```

### 2. Configure Environment

Edit `.env`:
```bash
# Oracle Public Channel
ORACLE_TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
ORACLE_TELEGRAM_CHANNEL_ID=-1001234567890
ORACLE_CHANNEL_USERNAME=@pythia_oracle

# LLM Configuration
ORACLE_LLM_PROVIDER=mistral
ORACLE_LLM_MODEL_REASONING=mistral-small-2503

# Publishing Thresholds
ORACLE_MIN_EDGE=0.12
ORACLE_MIN_CONFIDENCE=0.60
ORACLE_MIN_VOLUME_USD=500000
ORACLE_MIN_DAYS_UNTIL_CLOSE=3
ORACLE_MAX_DAYS_UNTIL_CLOSE=45
ORACLE_EXCLUDE_CATEGORIES=sports
ORACLE_MAX_LLM_COST_PER_DAY=5.00
```

### 3. Apply Database Migration

```bash
cd /Users/gerwer/Desktop/Pythia

# Apply migration
sqlite3 data/pythia.db < sql/migrations/0011_oracle_prophecies.sql

# Verify tables created
sqlite3 data/pythia.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'oracle%';"
```

### 4. Test Components (Dry Run)

```bash
# Test publisher
python python/oracle_publisher.py --dry-run

# Test score updater
python python/oracle_score_updater.py

# Test scorecard
python python/oracle_scorecard.py

# Test backfill (preview only)
python python/oracle_backfill.py --limit 5 --dry-run
```

### 5. Backfill Archive (ONE-TIME)

```bash
# IMPORTANT: Канал должен быть CLOSED (invite-only) на этом этапе

# Run backfill (25 predictions, silent notifications)
python python/oracle_backfill.py --limit 25 --sleep 2

# После завершения:
# - Проверь канал визуально
# - Убедись что win rate > 65%
# - Проверь форматирование постов
```

### 6. Configure Cron Jobs

Add to `supercronic.crontab`:
```bash
# Oracle daily prophecy (06:30 UTC, after forecast)
30 6 * * * /app/scripts/entrypoint.sh oracle-publish

# Oracle score updater (19:00 UTC, after resolution)
0 19 * * * /app/scripts/entrypoint.sh oracle-update-scores

# Oracle weekly scorecard (Sunday 10:00 UTC)
0 10 * * 0 /app/scripts/entrypoint.sh oracle-scorecard
```

Create wrapper in `scripts/entrypoint.sh`:
```bash
elif [ "$1" = "oracle-publish" ]; then
  exec python /app/python/oracle_publisher.py

elif [ "$1" = "oracle-update-scores" ]; then
  exec python /app/python/oracle_score_updater.py

elif [ "$1" = "oracle-scorecard" ]; then
  exec python /app/python/oracle_scorecard.py
```

### 7. Deploy to Production

```bash
# Push to GitHub
git add .
git commit -m "feat: Oracle public channel implementation"
git push origin feature/oracle-public-channel

# SSH to server
ssh pythia@amper

# Pull changes
cd /home/pythia/pythia
git fetch origin
git checkout feature/oracle-public-channel
git pull

# Apply migration
docker exec pythia-scheduler sqlite3 /app/data/pythia.db < /app/sql/migrations/0011_oracle_prophecies.sql

# Update .env with ORACLE_* vars
nano .env

# Restart services
docker-compose restart pythia-scheduler

# Test dry-run
docker exec pythia-scheduler python /app/python/oracle_publisher.py --dry-run

# Run backfill
docker exec pythia-scheduler python /app/python/oracle_backfill.py --limit 25 --sleep 3
```

## Monitoring & Maintenance

### Health Checks

**Daily:**
```bash
# Check if prophecy was published today
sqlite3 data/pythia.db "SELECT published_at, market_question FROM oracle_prophecies WHERE published_at >= date('now') AND is_backfilled = 0;"

# Check logs
tail -50 logs/oracle_publisher.log
tail -50 logs/oracle_score_updater.log
```

**Weekly:**
```bash
# Check win rate
sqlite3 data/pythia.db "SELECT accuracy_7d, accuracy_30d, current_streak FROM oracle_metrics WHERE id = 1;"

# Check for unresolved prophecies
sqlite3 data/pythia.db "SELECT COUNT(*) FROM oracle_prophecies WHERE resolved_at IS NULL AND market_closes_at < datetime('now');"
```

### Troubleshooting

**Problem:** No prophecy published today  
**Diagnosis:**
```bash
# Check if there were signals
sqlite3 data/pythia.db "SELECT COUNT(*) FROM predictions WHERE forecast_timestamp >= date('now') AND is_valid = 1;"

# Check logs for skip reasons
grep "SKIP" logs/oracle_publisher.log | tail -20
```

**Problem:** Telegram API errors  
**Solution:**
- Check bot token validity
- Verify bot is admin in channel
- Check rate limits (max 30 msg/sec)

**Problem:** LLM cost too high  
**Solution:**
- Reduce `ORACLE_MAX_LLM_COST_PER_DAY`
- Switch to cheaper model (deepseek vs mistral)
- Use template reasoning fallback

## Metrics & KPIs

**Track these metrics weekly:**

| Metric | Target | Critical Threshold |
|--------|--------|-------------------|
| Win Rate (30d) | >65% | <55% → PAUSE |
| Avg Brier (30d) | <0.22 | >0.30 → REVIEW |
| Daily publish rate | 100% | <80% → DEBUG |
| Resolution lag | <24h | >72h → MANUAL |
| LLM cost/day | <$1 | >$5 → ALERT |

**Access metrics:**
```bash
sqlite3 data/pythia.db "SELECT * FROM oracle_metrics WHERE id = 1;"
```

## Safety & Compliance

**Immutable Audit Trail:**
- Каждое prophecy имеет `telegram_message_id`
- Telegram API не позволяет удалить/изменить сообщение без следа
- `published_at` timestamp доказывает когда было опубликовано

**Disclaimer (in every post):**
- "Not financial advice, DYOR"
- Channel bio: "For informational purposes only"
- No direct financial promises

**Privacy:**
- Не собираем user data
- Не храним IP/logs подписчиков
- GDPR compliant (public channel, no personal data)

## Future Enhancements

**Phase 2 (after launch):**
- [ ] Public dashboard (React web app)
- [ ] Multi-signal publishing (2-3 per day)
- [ ] Interactive polls (user predictions)
- [ ] Category-specific channels
- [ ] Real-time updates via WebSocket

**Phase 3 (scale):**
- [ ] Premium tier (paid Telegram channel)
- [ ] API for developers
- [ ] Mobile app
- [ ] Multi-language support

## Resources

**Documentation:**
- Master plan: `/Users/gerwer/Desktop/MemPalace/project/PYTHIA_ORACLE_PLAN_2026-06-26.md`
- Marketing strategy: (included in master plan)
- Technical specs: This file

**Monitoring:**
- Logs: `logs/oracle_*.log`
- Metrics: `oracle_metrics` table
- Telegram: https://t.me/pythia_oracle (будет создан)

**Support:**
- Issues: Create in MemPalace memory
- Updates: Track in `PYTHIA_MEMORY.md`

---

**Last Updated:** 2026-06-26  
**Maintainer:** Pythia Team  
**Status:** Pre-Launch Development
