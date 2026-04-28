# Развертывание Pythia на `amper`

Дата актуализации: 2026-04-28

## Роль документа

Это production-runbook для текущей Pythia.

Сначала прочитайте:

- [README.md](../README.md)
- [docs/README.md](./README.md)
- [docs/PYTHIA_V2_WORKSTREAM.md](./PYTHIA_V2_WORKSTREAM.md)

## Базовая топология

- хост: `amper`
- системный пользователь проекта: `pythia`
- рабочая директория: `/home/pythia/pythia`
- Pythia обновляется только через локальный `docker compose` из этой директории
- dashboard публикуется на `127.0.0.1:8787` и наружу идёт только через reverse proxy

На этом же сервере живёт отдельный проект `sproutsignal`.

Критичное правило:

- никаких глобальных Docker-команд по всему хосту;
- никаких `docker system prune`, `docker stop $(docker ps -q)` и похожих действий;
- управлять только контейнерами Pythia из `/home/pythia/pythia`.

## Что сейчас считается production Pythia

В production-контур сейчас входят:

- daily forecast;
- decision core;
- paper/shadow lifecycle;
- autonomous watchlist engine;
- attention queue;
- dashboard;
- Telegram operator loop.

В production-контур пока не входят:

- live trading;
- реальные trading-ключи;
- live-capital execution;
- Dual-Loop Fast Loop как отдельный постоянно работающий production stack.

Fast Loop код уже в репозитории, но его verification/deployment нужно считать отдельным workstream поверх основного Pythia runtime.

## `.env` и секреты

Файл `.env` на сервере считается источником секретов.

Правила:

- не удалять `.env`;
- не перетирать `.env` шаблоном;
- перед обновлением убедиться, что файл на месте;
- новые переменные добавлять вручную или через безопасное слияние.

## Первый деплой

Работайте только под пользователем `pythia`.

```bash
cd /home/pythia/pythia
bash scripts/day0_check.sh
docker compose build
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
docker compose up -d
```

## Стандартное обновление production

Если нужно обновить сервер на конкретную ветку:

```bash
cd /home/pythia/pythia
git fetch origin
git checkout <target-branch>
git pull --ff-only origin <target-branch>
test -f .env
docker compose down
docker compose build
docker compose up -d
bash scripts/manual_run.sh status
```

Если обновление затрагивает watchlist-контур, полезно сразу прогнать:

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
```

## Что проверить после обновления

### Контейнеры

```bash
docker compose ps
```

Ожидаемо:

- `pythia-dashboard` healthy
- `pythia-scheduler` healthy
- `pythia-telegram` healthy

### Dashboard

```bash
docker compose logs --tail=50 pythia-dashboard
curl -s http://127.0.0.1:8787/api/auth/me
```

### Telegram

```bash
docker compose logs --tail=50 pythia-telegram
```

Проверьте в личном чате:

- `/help`
- `/status`
- `/health`

### Watchlist / attention

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
```

Ожидаемо:

- refresh проходит без падений;
- если есть значимый `watch update`, он доходит до Telegram;
- `Избранное` и `Сводка` в dashboard отвечают без ошибок.

## Отдельная проверка Fast Loop на сервере

Если локальная toolchain-среда недоступна, Fast Loop можно проверять на сервере временными контейнерами, примонтировав `/home/pythia/pythia`.

Минимальные цели проверки:

- `cargo check` и `cargo clippy` для `services/`;
- `bun run lint` и `tsc --noEmit` для TypeScript-кода;
- запуск инфраструктуры без panic/throw.

Эта проверка не заменяет production deploy Slow Loop, а дополняет его.

`redis` и `postgres` в Dual-Loop dev stack доступны только внутри compose-сети и не публикуются на хост. Это сделано специально, чтобы shared-хосты вроде `amper` не конфликтовали с другими проектами.

Для запуска Fast Loop достаточно обычной команды:

```bash
docker compose -f docker-compose.dev.yml up -d redis nats postgres gateway-api sniper-ingest sniper-executor
```

Если нужен разовый доступ к этим сервисам с хоста, используйте `docker compose exec`, а не постоянную публикацию портов.

## Reverse proxy

Наружу dashboard должен идти только так:

```text
internet -> reverse proxy -> 127.0.0.1:8787 -> pythia-dashboard
```

## Диагностика

Минимальный маршрут:

```bash
bash scripts/manual_run.sh status
bash scripts/manual_run.sh logs
docker compose ps
```

Если проблема в scheduler/watchlist-контуре:

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
docker compose logs --tail=100 pythia-scheduler
```
