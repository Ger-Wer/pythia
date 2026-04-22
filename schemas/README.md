# Dual-Loop Schemas

Protobuf-схемы, описывающие контракты обмена между Slow Loop (Pythia, TypeScript) и Fast Loop (Sniper, Rust).

**Single source of truth.** Любое сообщение, пересекающее границу сервисов, должно быть описано здесь.

## Структура

```
schemas/
├── buf.yaml             # buf module config (lint + breaking)
├── buf.gen.yaml         # генерация клиентов для Rust (prost) и TS (ts-proto)
└── dual_loop/v1/
    ├── common.proto     # общие типы (Timestamp, Money, MarketId)
    ├── watchlist.proto  # pythia → sniper: управление watchlist
    ├── signal.proto     # pythia → gateway → UI: сигналы и решения
    ├── trigger.proto    # sniper-ingest → sniper-executor: ончейн триггеры
    ├── execution.proto  # sniper-executor → all: submitted/filled/reverted
    └── control.proto    # gateway → sniper: kill switch
```

## Команды

```bash
# Lint
cd schemas && buf lint

# Проверка breaking changes против main
cd schemas && buf breaking --against '.git#branch=main,subdir=schemas'

# Генерация клиентов
cd schemas && buf generate
```

Эти команды автоматизированы в `Makefile` (`make proto-lint`, `make proto-gen`, `make proto-breaking`).

## Версионирование

- Пакет: `dual_loop.v1`.
- Все добавления — через `optional` поля (backward-compatible).
- Breaking changes → новый пакет `dual_loop.v2`, старый живёт до миграции всех потребителей.
- CI блокирует merge при нарушении `buf breaking`.

См. [`docs/dual_loop/04-bus-protocol.md`](../docs/dual_loop/04-bus-protocol.md) для деталей протокола.
