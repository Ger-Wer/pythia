# Развертывание Pythia на `amper`

Дата актуализации: 2026-04-08

## Роль документа

Это production-runbook: как развернуть, обновить и проверить Pythia на `amper`.

Если нужно понять, что система делает и почему она так устроена, сначала читайте:

- [README.md](/Users/gerwer/Desktop/Pythia/README.md)
- [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md)
- [docs/AUDIT.md](/Users/gerwer/Desktop/Pythia/docs/AUDIT.md)

## Что именно вы деплоите

Сейчас production включает:

- daily forecast;
- decision core;
- autonomous watchlist engine;
- dashboard;
- Telegram operator/fallback bot.

Сейчас production не включает:

- live trading;
- хранение реальных trading-ключей;
- attention engine.

## Где это живет

- хост: `amper`
- пользователь: `pythia`
- каталог: `/home/pythia/pythia`
- dashboard: `127.0.0.1:8787`, наружу только через reverse proxy

## Обязательные переменные окружения

Скопируйте:

```bash
cp .env.example .env
```

Заполните как минимум:

- LLM backend credentials;
- `EXA_API_KEY`;
- `TAVILY_API_KEY`;
- Telegram credentials;
- `DASHBOARD_USERNAME`;
- `DASHBOARD_PASSWORD_HASH`;
- `DASHBOARD_SESSION_SECRET`;
- `DASHBOARD_PUBLIC_BASE_URL`.

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

## Что проверить после первого деплоя

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

Ожидаемо: API отвечает, dashboard открывается через reverse proxy.

### Telegram

```bash
docker compose logs --tail=50 pythia-telegram
```

В личном чате проверьте:

- `/help`
- `/status`
- `/health`

### Watchlist engine

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
```

Ожидаемо:

- команда отрабатывает без падения;
- если есть значимый `watch update`, он доходит в Telegram;
- `Избранное` в dashboard показывает `watch dossiers` и updates.

## Что должен увидеть оператор

После успешного деплоя руками проверьте:

1. `Сводка` открывается без ошибок.
2. `Сигналы` показывает рынки последнего прогона.
3. В `Разборе рынка` верх экрана начинается с dossier-first блоков, а не с dump-страницы.
4. `Избранное` показывает `watch dossiers`, а не просто список `market_id`.
5. Telegram присылает короткие `watch updates`, а не сырые логи.

## Обновление production

Стандартный update:

```bash
cd /home/pythia/pythia
git pull
docker compose build
docker compose up -d
bash scripts/manual_run.sh status
```

Если обновление затрагивает watchlist-контур, сразу после этого полезно прогнать:

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
```

## Расписание scheduler

- `forecast` - 06:00 UTC
- `watchlist refresh` - каждые 6 часов
- `watch alerts` - через 10 минут после каждого refresh
- `resolution` - 18:00 UTC
- `weekly report` - воскресенье, 20:00 UTC

## Reverse proxy

Dashboard не должен публиковаться наружу напрямую из контейнера.

Нужна схема:

```text
internet -> reverse proxy -> 127.0.0.1:8787 -> pythia-dashboard
```

Текущий production hostname:

- `https://b1w.duckdns.org`

## Правила безопасности

- работать только как пользователь `pythia`;
- не смешивать Pythia с `SproutSignal` по сетям и томам;
- не публиковать dashboard напрямую наружу;
- не хранить trading-ключи;
- не выполнять bare-metal операции вместо предусмотренных `manual_run.sh` и compose-команд.

## Если что-то пошло не так

Минимальный маршрут диагностики:

```bash
bash scripts/manual_run.sh status
bash scripts/manual_run.sh logs
docker compose ps
```

Если проблема связана с watchlist:

```bash
bash scripts/manual_run.sh watchlist-refresh
bash scripts/manual_run.sh watch-alerts
docker compose logs --tail=100 pythia-scheduler
```
