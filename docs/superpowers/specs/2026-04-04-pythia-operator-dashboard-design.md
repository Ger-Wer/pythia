# Pythia Operator Dashboard Design

Дата: 2026-04-04

## 1. Контекст

`Pythia` уже вышла из стадии, где Telegram остаётся удобным единственным интерфейсом оператора.

Сейчас Telegram хорошо подходит для:

- алертов;
- коротких статусов;
- быстрых команд;
- аварийной диагностики.

Но Telegram плохо подходит для:

- сравнения нескольких рынков сразу;
- чтения длинного reasoning;
- истории одного рынка по дням;
- watchlist;
- operator workflow на ноутбуке и телефоне;
- сочетания просмотра данных и запуска действий в одном месте.

Новый dashboard нужен не как замена forecasting pipeline, а как основная операторская консоль поверх уже работающей `Pythia`.

## 2. Цель

Построить приватный internet-facing dashboard для одного оператора на `b1w.duckdns.org`, который:

- даёт понятный визуальный доступ к текущему состоянию `Pythia`;
- позволяет анализировать рынки без Telegram-команд;
- позволяет запускать только безопасные операторские действия;
- не превращается в открытую web-админку сервера.

## 3. Не-цели

В первую фазу dashboard не должен:

- редактировать `.env`;
- менять модели, лимиты и фильтры;
- выполнять shell-команды;
- управлять Docker вне whitelisted действий;
- делать destructive database actions;
- заменять SSH для системного администрирования;
- открывать публичный доступ без защиты;
- становиться публичным продуктом для множества пользователей.

Полноценный `Admin Control Plane` — отдельная будущая фаза, не часть этого дизайна.

## 4. Продуктовое решение

Принимается гибридная модель:

- `Telegram` остаётся каналом алертов, коротких статусов и быстрых действий;
- `Dashboard` становится основным рабочим местом оператора.

То есть:

- глазами оператор работает в dashboard;
- Telegram сообщает, что произошло;
- SSH остаётся только для редких инфраструктурных операций.

## 5. Выбранный стек

### Frontend

- `React + Vite`

### Backend

- отдельный `Python API`

### Источник данных

- `SQLite`
- `logs/`
- текущий operator/runtime state

### Runtime-модель

- новый сервис `pythia-dashboard`
- `pythia-scheduler` и `pythia-telegram` остаются как есть

## 6. Общая архитектура

Архитектура делится на четыре слоя:

1. `Operator Core`
2. `Dashboard API`
3. `Dashboard Frontend`
4. `Edge / Reverse Proxy`

### 6.1. Operator Core

Это общий слой бизнес-логики оператора, который используют и Telegram, и dashboard.

Он отвечает за:

- чтение overview/status/health;
- чтение последнего прогона;
- чтение деталей рынка;
- работу с watchlist;
- запуск whitelisted operator actions;
- сбор коротких логов и служебных summary.

Он не должен содержать:

- Telegram formatting;
- HTML/SPA presentation;
- auth/session;
- reverse-proxy логику.

### 6.2. Dashboard API

Это новый Python HTTP-слой поверх `Operator Core`.

Его задача:

- отдать JSON для frontend;
- защитить действия через auth и policy;
- не дублировать доменную/операторскую логику.

### 6.3. Dashboard Frontend

Это отдельное React/Vite приложение в собственной директории.

Его задача:

- показывать оператору данные и действия в человеко-понятной форме;
- не знать о SQL, Docker и внутренней логике прогонов;
- общаться только через API.

### 6.4. Edge / Reverse Proxy

Наружу публикуется только `pythia-dashboard`.

Снаружи не должны публиковаться:

- `pythia-scheduler`
- `pythia-telegram`

## 7. UX Operator Console v1

### 7.1. Главный экран `Overview`

Показывает:

- system status;
- research status;
- timestamp последнего прогона;
- число валидных прогнозов;
- число resolved;
- 3-5 самых важных текущих расхождений;
- быстрые безопасные действия.

### 7.2. Экран `Latest Run`

Таблица последнего прогона:

- рынок;
- категория;
- цена рынка;
- `Research+R1`;
- `Naked R1`;
- edge;
- confidence;
- research status;
- переход в детали.

### 7.3. Экран `Market Detail`

Один рынок целиком:

- headline;
- рыночная цена;
- `Research+R1`;
- `Naked R1`;
- edge;
- история по дням;
- reasoning;
- key risk;
- catalyst;
- primary driver;
- confirming signals;
- disconfirming signal;
- sources;
- watchlist actions.

### 7.4. Экран `Watchlist`

Минимум:

- список отмеченных рынков;
- последняя оценка;
- последнее изменение;
- короткая заметка оператора;
- add/remove.

### 7.5. Экран `Operations`

Только безопасные действия:

- `Run forecast`
- `Run resolve`
- `Run weekly`
- `Refresh status`
- `Open logs`

Каждое действие должно иметь понятный статус:

- `queued`
- `running`
- `success`
- `failed`

## 8. Ментальная модель интерфейсов

После внедрения dashboard роли интерфейсов такие:

- `Telegram` = уведомить, что что-то произошло;
- `Dashboard` = понять, что это значит и что делать;
- `SSH` = редкое системное сопровождение.

Цель — убрать split-brain workflow, где оператор смотрит данные в одном интерфейсе, а действует в другом.

## 9. Безопасность

Dashboard публикуется на `b1w.duckdns.org` как private internet-facing interface.

Нужны два слоя защиты:

### 9.1. Внешний защитный слой

На уровне reverse proxy:

- HTTPS only;
- redirect HTTP -> HTTPS;
- auth на уровне proxy;
- security headers;
- rate limiting;
- ограничение размера запросов;
- скрытие всего, кроме dashboard host.

### 9.2. Внутренний защитный слой

Внутри самого dashboard:

- login page;
- password hash;
- session cookie;
- timeout;
- logout;
- auth guard на API routes.

### 9.3. Политика действий

Даже после входа dashboard не должен быть shell-like интерфейсом.

Разрешены только whitelisted operator actions:

- `forecast`
- `resolve`
- `weekly`
- `refresh`
- `logs`
- `watchlist add/remove`

Запрещены:

- shell;
- произвольные команды;
- изменение конфигов;
- Docker/system admin;
- destructive db operations.

## 10. Сетевое размещение

### Внутренняя сеть

`pythia_internal`:

- `pythia-scheduler`
- `pythia-telegram`
- `pythia-dashboard`

### Edge-сеть

Контролируемая сеть edge/proxy:

- reverse proxy
- `pythia-dashboard`

Только `pythia-dashboard` подключается к edge-сети и только он доступен извне.

## 11. Размещение по файлам

### Python

```text
python/
  operator_core.py
  dashboard_api.py
  dashboard_auth.py
  dashboard_models.py
  operator_actions.py
  telegram_operator.py
```

Роли:

- `operator_core.py` — общая operator business logic
- `dashboard_api.py` — API routes
- `dashboard_auth.py` — login/session/auth
- `dashboard_models.py` — pydantic/response models
- `operator_actions.py` — Telegram-facing compatibility layer поверх operator core

### Frontend

```text
dashboard/
  package.json
  vite.config.ts
  src/
    main.tsx
    app/
    pages/
    components/
    api/
    types/
```

### Runtime

Новый Docker service:

- `pythia-dashboard`

Он обслуживает:

- API
- static frontend bundle

## 12. Watchlist storage

Watchlist хранится в `SQLite`.

Минимальная таблица:

- `market_id`
- `note`
- `created_at`
- `updated_at`

Это выбранный путь вместо JSON-файла ради простоты и устойчивости.

## 13. API-контракт v1

### Read endpoints

- `GET /api/overview`
- `GET /api/runs/latest`
- `GET /api/markets/:marketId`
- `GET /api/watchlist`
- `GET /api/health`
- `GET /api/logs`

### Action endpoints

- `POST /api/actions/forecast`
- `POST /api/actions/resolve`
- `POST /api/actions/weekly`
- `POST /api/watchlist`
- `DELETE /api/watchlist/:marketId`

### Auth endpoints

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

## 14. Фазирование реализации

Реализация идёт по фазам:

1. Выделить `operator_core`
2. Добавить watchlist в SQLite
3. Поднять внутренний `Dashboard API`
4. Реализовать React/Vite frontend
5. Собрать сервис `pythia-dashboard`
6. Добавить app-auth
7. Публиковать через reverse proxy на `b1w.duckdns.org`
8. Упростить Telegram до alert-first роли

Запрещается публиковать dashboard наружу до появления app-auth.

## 15. Критерий успеха

Dashboard считается удачным, если оператор может:

- открыть `Overview` и за `10-30` секунд понять состояние системы;
- открыть важный рынок без знания `market_id`;
- выполнить `forecast/resolve/weekly` без Telegram;
- работать с watchlist без запоминания рынков в голове;
- не использовать Telegram как основной рабочий интерфейс.

## 16. Финальная рекомендация

Правильное направление для `Pythia` — не “Telegram или dashboard”, а:

- `Telegram` как alert channel и quick actions;
- `Dashboard` как основной operator console;
- `SSH` как редкий системный путь.

Первой фазой проектируется и реализуется только `Operator Console`, без полноценного `Admin Control Plane`.
