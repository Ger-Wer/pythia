# Pythia

**Дата актуализации: 2026-06-26**

Pythia - это приватный decision desk для Polymarket. Полностью функциональная система с двумя контурами.

---

## 🎯 Текущий статус

### ✅ Slow Loop (Production)
**Статус**: Работает на amper (144.24.254.226)

- **Forecast pipeline**: Восстановлен (2026-06-26) после 42-дневного простоя
- **LLM**: Mistral AI (mistral-small/medium)
- **Schedule**: Daily forecast runs в 06:00 UTC
- **Telegram bot**: Активен для operator triage
- **Shadow PG**: Готов к финальной миграции

**Компоненты:**
- Daily forecast (research + synthesis)
- Decision journal & watchlist tracking
- Paper/shadow execution logs
- Dashboard (React)
- Telegram operator interface

### 🚀 Fast Loop - Gate #10 M4 (Ready to Deploy)
**Статус**: PRODUCTION-READY (2026-06-26)

**Multi-market orderbook driver:**
- ✅ Multi-market orchestration (N trackers одновременно)
- ✅ Live Polymarket integration (REST + WebSocket)
- ✅ Watchlist integration (SQLite init + NATS updates)
- ✅ Fault tolerance (circuit breaker + exponential backoff)
- ✅ Backpressure control (memory/CPU/tracker limits)
- ✅ Graceful shutdown (concurrent с timeouts)
- ✅ Health monitoring per tracker

**Deployment:**
```bash
cd services/sniper-orderbook
cargo build --release --bin driver
./target/release/driver
```

**Конфигурация (env vars):**
```bash
NATS_URL=nats://127.0.0.1:4222
DRIVER_DB_PATH=/app/data/pythia.db
DRIVER_MAX_TRACKERS=100
DRIVER_MEMORY_THRESHOLD_MB=2048
```

**Документация:**
- Spec: `docs/superpowers/specs/2026-06-26-gate10-m4-multi-market-driver-design.md`
- Plan: `docs/superpowers/plans/2026-06-26-gate10-m4-multi-market-driver.md`
- Component README: `services/sniper-orderbook/README.md`

---

## Что делает Pythia

1. **Находит сильные рынки** - daily forecast по всем активным Polymarket рынкам
2. **Сопровождает watchlist** - operator triage через Telegram
3. **Копит paper dataset** - shadow execution без реальных денег
4. **Tracking real-time orderbook** (M4) - multi-market driver для Fast Loop

---

## Архитектура

```
Slow Loop (TypeScript/Python)          Fast Loop (Rust/TypeScript)
├── forecast (research + synthesis)    ├── sniper-orderbook (M4 driver)
├── watchlist tracking                 ├── sniper-ingest
├── decision journal                   ├── sniper-risk
├── dashboard (React)                  ├── sniper-executor (paper mode)
└── telegram bot                       └── gateway-api
```

**Инфраструктура:**
- **Databases**: PostgreSQL (main), Shadow PG (replica), SQLite (watchlist)
- **Message bus**: NATS
- **Deployment**: Docker Compose на amper
- **Monitoring**: Logs + manual health checks

---

## Quick Start

### Development

**Prerequisites:**
- Node.js 18+
- Python 3.11+
- Rust 1.70+
- Docker + Docker Compose

**Local setup:**
```bash
# Clone
git clone git@github.com:Ger-Wer/pythia.git
cd pythia

# Install dependencies
npm install
cd python && pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env with your keys

# Run locally
npm run dev        # Slow Loop services
cd services/sniper-orderbook && cargo run --bin driver  # Fast Loop driver
```

### Production Deployment

**Amper (144.24.254.226):**
```bash
ssh amper
cd /home/pythia/pythia
git pull origin main
docker compose down && docker compose up -d --build
```

**Важно:**
- Любые Docker действия только из `/home/pythia/pythia`
- Не использовать глобальные Docker cleanup команды (SproutSignal соседствует)
- Перед рестартом проверить `.env` актуальность

---

## Структура репозитория

```
pythia/
├── src/              # Slow Loop TypeScript services
├── python/           # Forecast pipeline
├── dashboard/        # React UI
├── services/         # Fast Loop Rust components
│   ├── sniper-orderbook/  # M4 multi-market driver ⭐
│   ├── sniper-ingest/
│   ├── sniper-risk/
│   └── sniper-executor/
├── gateway-api/      # REST API
├── schemas/          # Shared types
├── infra/            # Docker configs
├── docs/             # Specs & plans
└── archive/          # Историческая документация
```

---

## Ключевые документы

**Specs & Plans (актуальные):**
- M4 Driver: `docs/superpowers/specs/2026-06-26-gate10-m4-multi-market-driver-design.md`
- Dual Loop: `docs/dual_loop/` (Phase 0-2 complete)
- Deployment: `docs/DEPLOYMENT.md`
- Decisions: `docs/DECISION_LOG.md`

**Историческое:**
- `archive/` - старые design docs, отчёты, устаревшие specs

---

## Production Operations

**Logs:**
```bash
# Slow Loop
docker compose logs -f pythia-scheduler
docker compose logs -f pythia-forecast

# Fast Loop (когда deployed)
docker compose logs -f sniper-orderbook-driver
```

**Health Checks:**
```bash
# Forecast status
ssh amper "cd /home/pythia/pythia && npm run forecast:check"

# Database parity
./scripts/shadow_pg_parity.sh

# Driver status (когда deployed)
curl http://localhost:8080/health  # TODO: implement /health endpoint
```

**Incident Response:**
1. Check logs
2. Verify `.env` configuration
3. Restart affected service: `docker compose restart <service>`
4. If DB issue: check Shadow PG parity
5. Escalate if unresolved

---

## Roadmap

**Near-term (Q2-Q3 2026):**
- [x] Gate #10 M4: Multi-market driver (DONE 2026-06-26)
- [ ] Deploy M4 driver to production
- [ ] Metrics/observability (Task 9)
- [ ] NATS orderbook publishing
- [ ] Shadow PG final migration

**Medium-term (Q4 2026):**
- [ ] Pythia v1.0 Paper Operator Release
- [ ] Command Center auth
- [ ] Kill-switch implementation
- [ ] Audit trail hardening

**Long-term (2027+):**
- [ ] Live trading (requires audit, compliance, risk management)
- [ ] Horizontal scaling (multi-instance coordination)
- [ ] Advanced analytics

---

## Contributing

**Workflow:**
1. Create feature branch from `main`
2. Implement + tests
3. Review (spec compliance + code quality)
4. PR → `main`
5. Deploy to amper

**Standards:**
- TDD для критических путей
- Two-stage review (spec + quality)
- Русская документация в README
- Английские commit messages
- No secrets in git

---

## Support & Contact

**Issues**: GitHub Issues  
**Internal docs**: MemPalace canonical memory  
**Server access**: SSH keys required

**Critical rules:**
- Любые тесты только на production сервере (не локально)
- Не удалять данные без явного подтверждения
- Не коммитить secrets/env files
- Не трогать Docker на amper глобально

---

**Last updated**: 2026-06-26 - Gate #10 M4 complete, forecast pipeline restored
