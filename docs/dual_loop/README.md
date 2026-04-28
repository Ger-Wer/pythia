# Dual-Loop Architecture

Дата актуализации: 2026-04-28
Статус: Design v1.1, Phase 0 completed, Phase 1 mostly merged, Phase 2 partially implemented

## Роль документа

Этот каталог описывает целевую архитектуру **Dual-Loop**: объединение существующей Pythia (Slow Loop / Radar) и проектируемого Sniper (Fast Loop / Cannon) в единую систему latency-arbitrage на Polymarket.

Этот документ **не заменяет** действующие:
- [`docs/PYTHIA_V2_WORKSTREAM.md`](../PYTHIA_V2_WORKSTREAM.md) — source of truth по текущей Pythia v2.
- [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md) — runtime-контуры Pythia как они есть сейчас.

Dual-Loop — это следующая фаза развития. Пока существующая Pythia продолжает работать без регрессий, рядом наращивается новая инфраструктура (bus, observability, Rust-сервисы), и только в Phase 3 производится live-execution.

## Карта документов

| # | Файл | Что внутри |
|---|------|------------|
| 00 | [`00-overview.md`](./00-overview.md) | C4 Level 1 (System Context), цели системы, акторы |
| 01 | [`01-containers.md`](./01-containers.md) | C4 Level 2 (Containers), SLO, технологический стек |
| 02 | [`02-components.md`](./02-components.md) | C4 Level 3 (Components) по Slow/Fast Loop |
| 03 | [`03-data-flow.md`](./03-data-flow.md) | Сквозной data flow от обнаружения рынка до исполнения |
| 04 | [`04-bus-protocol.md`](./04-bus-protocol.md) | Протокол связи Node ↔ Rust, топики, schema-evolution |
| 05 | [`05-command-center-ui.md`](./05-command-center-ui.md) | UI/UX Command Center, layout, виджеты, hotkeys |
| 06 | [`06-roadmap.md`](./06-roadmap.md) | Roadmap 5 фаз с DoD |
| 07 | [`07-risks.md`](./07-risks.md) | Risk assessment + паттерны mitigation'а |
| 08 | [`08-tech-decisions.md`](./08-tech-decisions.md) | Обоснование выбора технологий + North Star метрики |

## Статус реализации

- [x] Design v1.0/v1.1 написан.
- [x] **Phase 0 (Foundations)** завершена.
- [~] **Phase 1 — Bus & Contracts** в основном уже в `main`.
- [~] **Phase 2 — Sniper MVP** частично уже в `main`: `sniper-ingest` и `sniper-risk` реализованы, в `sniper-executor` уже есть paper lifecycle/control foundation, но end-to-end paper verification еще впереди.
- [ ] Phase 3 — Live Execution (hardened).
- [ ] Phase 4 — Scale & Optimization.

Важное архитектурное решение на текущий момент: `phase1b-pg-listen-notify` не считается каноническим transport-path. Межсервисный source of truth остается Redis/NATS bus; LISTEN/NOTIFY допускается только как best-effort side-channel.

Текущее transitional состояние шины: `gateway-api` уже умеет ingest'ить protobuf Redis payloads на receive-side, но его outbound operator mutations (`signals.decisions`, `control.kill`) пока еще публикуются как JSON compatibility envelope.

См. [`06-roadmap.md`](./06-roadmap.md) для деталей.

## Быстрая навигация по коду

| Слой | Путь в репо | Язык |
|------|-------------|------|
| Slow Loop (существующая Pythia) | `src/`, `python/` | TypeScript (Bun), Python |
| Schemas (общие контракты) | `schemas/dual_loop/v1/` | Protobuf |
| Fast Loop services | `services/sniper-ingest/`, `services/sniper-executor/`, `services/sniper-risk/` | Rust |
| Общие Rust-библиотеки | `services/common/` | Rust |
| Bus/infra | `docker-compose.dev.yml`, `infra/` | — |
| CI | `.github/workflows/dual-loop-ci.yml` | — |

## Принципы

1. **Additive, not destructive.** Новая инфраструктура не ломает существующую Pythia. Миграция SQLite → Postgres делается через shadow-write.
2. **Single source of truth для контрактов.** Все межсервисные payload'ы описаны в `schemas/dual_loop/v1/*.proto`. Breaking changes блокируются в CI.
3. **Human-in-the-loop.** Операция с капиталом требует подтверждения оператора. Auto-exec только после explicit `arm` по конкретному рынку.
4. **Fail-loud, fail-safe.** При деградации любого контура — система переходит в DRAIN (отменяет triggers), а не продолжает работу на stale данных.
