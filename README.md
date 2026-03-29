# Pythia

Pythia - это автономная система прогноза для Polymarket. Она отбирает подходящие рынки, запускает исследование и синтез вероятности, сохраняет результаты в SQLite, считает качество прогнозов и отправляет уведомления в Telegram.

## Архитектура

```text
Polymarket Gamma API
  -> отбор рынков
  -> mini-research / news-light слой
  -> базовый контрольный прогон Naked R1
  -> синтез вероятности
  -> SQLite + логи + Brier score
  -> Telegram-уведомления и отчеты
```

В текущем Docker-first пути Pythia запускается на `amper` под отдельным пользователем `pythia`. Для повседневной работы используется Telegram-оператор, а хостовые команды `docker compose` и `bash scripts/manual_run.sh` оставлены для развёртывания и диагностики.

## Быстрый старт на `amper`

Работайте на хосте `amper` под пользователем `pythia`.

Сначала создайте и заполните `.env` реальными значениями. Для запуска нужен либо прямой DeepSeek-доступ (`DEEPSEEK_API_KEY` и при необходимости `DEEPSEEK_BASE_URL`), либо OpenRouter-совместимые креды (`OPENROUTER_API_KEY` и `OPENROUTER_BASE_URL`). Для нормального качества research также задайте `EXA_API_KEY` или `TAVILY_API_KEY`.

```bash
cd /home/pythia/pythia
cp .env.example .env
```

После этого выполняйте команды в таком порядке:

```bash
bash scripts/day0_check.sh
docker compose build
bash scripts/manual_run.sh forecast
bash scripts/manual_run.sh status
docker compose up -d
```

Если нужно проверить поведение после старта, снова используйте `bash scripts/manual_run.sh status` и `bash scripts/manual_run.sh logs`. Для обычной операторской работы после запуска переключайтесь на Telegram-бота.

## Telegram-оператор

Pythia поддерживает операторский Telegram-бот без веб-дашборда. Он работает в личном чате, принимает команды только от авторизованного пользователя и возвращает ответы на русском языке.

Доступные slash-команды:

- `/help`
- `/status`
- `/forecast`
- `/resolve`
- `/weekly`
- `/day0`
- `/edge`
- `/logs`
- `/health`
- `/last_predictions`

Бот также понимает русские алиасы:

- `помощь`
- `статус`
- `прогноз`
- `логи`
- `здоровье`
- `последние`

## Операции

`bash scripts/manual_run.sh status` показывает состояние compose-сервиса и сводку по базе. Это полезно для хостовой проверки, но не заменяет Telegram-оператора.

`bash scripts/manual_run.sh logs` открывает журналы `pythia-scheduler` и `pythia-telegram`.

`bash scripts/manual_run.sh edge` считает edge и Brier score на накопленных данных.

Планировщик работает по расписанию `supercronic`:

- `forecast` - 06:00 UTC
- `resolution` - 18:00 UTC
- `weekly report` - воскресенье, 20:00 UTC

Обновление выполняется так: `git pull`, затем `docker compose build`, затем `docker compose up -d`, после чего проверьте `status` и при необходимости `logs`. Для ручного запуска сценариев используйте Telegram-бота, а не прямой bare-metal доступ.

## Критерий решения

Через 4 недели работы, когда накопится достаточно данных, запускайте:

```bash
bash scripts/manual_run.sh edge
```

Дальше ориентируйтесь на результат:

- `GO` - продолжать текущий подход.
- `PIVOT` - менять фильтры, промпты или порог отбора.
- `STOP` - если устойчивого преимущества нет.

## Стоимость

Отдельный VPS для Pythia не нужен: система работает прямо на `amper` в Docker-контейнере. Дополнительные расходы - только API и сервисы вроде Telegram, а не еще одна выделенная машина.
