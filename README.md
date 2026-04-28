# Pythia

Дата актуализации: 2026-04-28

Pythia - это приватный `decision desk` для Polymarket. В репозитории сейчас живут два связанных контура:

- `Slow Loop`: production-система на TypeScript/Python для daily forecast, decision journal, paper/shadow lifecycle, watchlist follow-up, dashboard и Telegram.
- `Fast Loop`: новый Dual-Loop контур для latency-sensitive watch/trigger/execution pipeline на Rust/TypeScript, пока без live trading.

## Что это за проект

Если коротко, Pythia решает три задачи:

1. Находит новые сильные рынки и формирует сигналы.
2. Сопровождает уже выбранные рынки через watchlist и operator triage.
3. Копит paper/shadow dataset для улучшения decision quality до любого live execution.

Проект уже давно не является "просто forecast-скриптом". Это гибридная система: зрелый operator desk + строящийся Fast Loop.

## На какой стадии проект

Состояние репозитория на сегодня:

- `Slow Loop` уже работает как production runtime на `amper`.
- `Phase 0` Dual-Loop завершена.
- `Phase 1` Dual-Loop по сути собрана: contracts, bus topics, `gateway-api`, Command Center preview, shadow PostgreSQL.
- `Phase 2` Dual-Loop частично реализована: `sniper-ingest`, `sniper-risk` и paper/control foundation в `sniper-executor` уже в коде.
- Live trading в production нет. Реальные trading-ключи не используются.

Практический вывод для нового разработчика:

- `src/`, `python/` и `dashboard/` не являются "старым кодом" - это действующий Slow Loop.
- `gateway-api/`, `services/`, `schemas/` и `infra/` не являются экспериментальным мусором - это активный Fast Loop workstream.
- Исторические отчёты и устаревшие design-docs вынесены в [`archive/`](./archive/README.md).

## С чего начинать новому разработчику

Прочитайте документы в таком порядке:

1. [docs/README.md](./docs/README.md)
2. [docs/PYTHIA_V2_WORKSTREAM.md](./docs/PYTHIA_V2_WORKSTREAM.md)
3. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
4. [docs/dual_loop/README.md](./docs/dual_loop/README.md)
5. [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)

Если вы работаете именно с Fast Loop, после этого сразу откройте [docs/dual_loop/06-roadmap.md](./docs/dual_loop/06-roadmap.md).

## Карта репозитория

```text
src/                 Slow Loop runtime (forecast, decision core, watchlist)
python/              Dashboard/Telegram/operator API layer
dashboard/           React UI for Pythia v2
gateway-api/         Fast Loop gateway and command/control bridge
services/            Rust services: sniper-ingest / sniper-risk / sniper-executor
schemas/             Protobuf contracts for Dual-Loop
infra/               Observability and local/dev infrastructure
docs/                Active documentation
archive/             Historical reports, design docs and session artifacts
```

## Что реально работает сейчас

### Slow Loop

- daily forecast по рынкам Polymarket;
- `Research+R1` и `Naked R1`;
- `decision core` с `signals`, `decisions`, `positions`, `reviews`, `postmortems`;
- paper/shadow lifecycle;
- autonomous watchlist engine;
- attention queue и operator triage;
- dashboard как основной интерфейс;
- Telegram как inbox/fallback operator surface.

### Fast Loop

- protobuf contracts с fixed-point wire format;
- canonical topics `dl.v1.*`;
- `gateway-api` + inbound protobuf bridge;
- Command Center preview;
- `sniper-ingest`;
- `sniper-risk`;
- paper/control foundation в `sniper-executor`.

## Что ещё не закончено

- end-to-end verification Fast Loop на реальном toolchain;
- live feed alignment между `gateway-api` и Command Center;
- полный protobuf control-plane без transitional JSON publish-side;
- long-running paper execution verification;
- любой live execution на Polymarket.

## Source of truth

Если документы расходятся, порядок доверия такой:

1. [docs/PYTHIA_V2_WORKSTREAM.md](./docs/PYTHIA_V2_WORKSTREAM.md)
2. [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)
3. [docs/dual_loop/06-roadmap.md](./docs/dual_loop/06-roadmap.md)
4. [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md)
5. [docs/DECISION_LOG.md](./docs/DECISION_LOG.md)

Всё, что вынесено в [`archive/`](./archive/README.md), полезно только как исторический контекст.

## Быстрый operational context

- production-хост: `amper`
- системный пользователь: `pythia`
- рабочая директория: `/home/pythia/pythia`
- действующий Pythia deploy: `docker compose`
- соседний независимый проект на этом же сервере: `sproutsignal`

Критичное правило: любые Docker-действия для Pythia выполнять только из `/home/pythia/pythia` через локальный `docker compose`, без глобальной очистки Docker host.
