# Документация Pythia

Дата актуализации: 2026-04-29

Этот каталог уже не хранит всё подряд. Ниже только документы, которые стоит считать живыми точками входа.

Текущий общий релизный ориентир: `Pythia v1.0 Paper Operator Release`, а не live-capital trading.

## Канонический набор

1. [README.md](../README.md)
   Что это за проект, из каких контуров он состоит и на какой он стадии.
2. [PYTHIA_V2_WORKSTREAM.md](./PYTHIA_V2_WORKSTREAM.md)
   Главный source of truth по состоянию Slow Loop и продуктовой дорожной карте.
3. [ARCHITECTURE.md](./ARCHITECTURE.md)
   Как разрезан код и где искать основные runtime-контуры.
4. [dual_loop/README.md](./dual_loop/README.md)
   Вход в Fast Loop / Dual-Loop направление.
5. [DEPLOYMENT.md](./DEPLOYMENT.md)
   Runbook для `amper` и правила безопасного обновления production.

## Дополнительные живые документы

- [DECISION_LOG.md](./DECISION_LOG.md) - журнал ключевых архитектурных и продуктовых решений.
- [dual_loop/06-roadmap.md](./dual_loop/06-roadmap.md) - детальная стадия Fast Loop по фазам и DoD.

## Что считать историей

- Всё, что лежит в [`../archive/`](../archive/README.md), больше не является текущим source of truth.
- Старые implementation/design notes в `docs/superpowers/` полезны только как исторический контекст или исходный замысел.

Если документ не входит в канонический набор выше, сначала проверьте, не архивный ли он.
