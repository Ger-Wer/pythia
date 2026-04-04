# Развертывание Pythia на `amper`

Эта инструкция описывает Docker-first развертывание Pythia на хосте `amper` под пользователем `pythia`.

Полное описание архитектуры, runtime-потока, ограничений и текущего состояния для внешнего аудита находится в [docs/AUDIT.md](AUDIT.md).

## Подготовка хоста

1. Создайте отдельного пользователя:

```bash
sudo adduser pythia
```

2. Добавьте пользователя в группу `docker`:

```bash
sudo usermod -aG docker pythia
```

3. Скопируйте `authorized_keys`, чтобы вход по SSH шел под `pythia`:

```bash
sudo install -d -m 700 /home/pythia/.ssh
sudo cp /root/.ssh/authorized_keys /home/pythia/.ssh/authorized_keys
sudo chown -R pythia:pythia /home/pythia/.ssh
sudo chmod 600 /home/pythia/.ssh/authorized_keys
```

4. Перейдите в оболочку пользователя `pythia` и создайте GitHub deploy key для доступа к репозиторию:

```bash
sudo -iu pythia
ssh-keygen -t ed25519 -f ~/.ssh/pythia_github_deploy_key -C "pythia deploy key"
cat ~/.ssh/pythia_github_deploy_key.pub
```

Добавьте публичный ключ в GitHub как deploy key для нужного репозитория.

5. Настройте `~/.ssh/config` в домашнем каталоге `pythia`:

```sshconfig
Host github-pythia
  HostName github.com
  User git
  IdentityFile ~/.ssh/pythia_github_deploy_key
  IdentitiesOnly yes
```

## Клонирование репозитория

Работайте как пользователь `pythia` и не запускайте развертывание из каталога `SproutSignal`.

```bash
sudo -iu pythia
cd /home/pythia
git clone git@github-pythia:YOUR_ORG/pythia.git
cd /home/pythia/pythia
```

## Настройка окружения

Скопируйте пример окружения:

```bash
cp .env.example .env
```

Заполните реальные ключи LLM, Telegram и поискового слоя. Для нормального research лучше держать сразу оба ключа: `EXA_API_KEY` как primary и `TAVILY_API_KEY` как fallback. Если оба отсутствуют или оба недоступны, Pythia продолжит работать в degraded mode без внешних новостей.

Для dashboard обязательно заполните:

- `DASHBOARD_USERNAME`
- `DASHBOARD_PASSWORD_HASH`
- `DASHBOARD_SESSION_SECRET`
- `DASHBOARD_PUBLIC_BASE_URL`
- `DASHBOARD_COOKIE_SECURE=true` (для production за reverse proxy с HTTPS)

## Проверка Day 0

Перед первым запуском проверьте доступность рынков:

```bash
bash scripts/day0_check.sh
```

## Сборка и ручная проверка

Соберите образ:

```bash
docker compose build
```

Запустите ручной прогноз:

```bash
bash scripts/manual_run.sh forecast
```

Проверьте состояние:

```bash
bash scripts/manual_run.sh status
```

## Фоновый запуск

Когда проверка прошла успешно, поднимите сервис в фоне:

```bash
docker compose up -d
```

Сервис работает без published ports, без общих сетей с другими проектами и без shared volumes. Pythia держит свои bind-mounted `data` и `logs` внутри `/home/pythia/pythia`.

## Проверка Telegram-оператора

После `docker compose up -d --build` проверьте операторский контейнер:

```bash
docker compose ps
docker compose logs --tail=50 pythia-telegram
```

В личном чате с ботом выполните базовые команды:

```text
/help
/health
/status
```

Если ответы не приходят, сначала проверьте, что `pythia-telegram` в `Up` или `healthy`, а затем смотрите его логи. Для повседневной работы используйте dashboard, а Telegram оставьте как резервный канал.

## Проверка dashboard

После `docker compose up -d --build` проверьте:

```bash
docker compose ps
docker compose logs --tail=50 pythia-dashboard
curl -I http://127.0.0.1:8787/api/auth/me
```

Ожидается HTTP 200 от `api/auth/me` и статус `healthy` у `pythia-dashboard`.

Далее настройте reverse proxy для `b1w.duckdns.org` на upstream `127.0.0.1:8787`.

## Расписание

Встроенный scheduler запускает задачи по UTC:

- `forecast` - 06:00
- `resolution` - 18:00
- `weekly report` - воскресенье, 20:00

## Обновление

1. Перейдите в `/home/pythia/pythia` под пользователем `pythia`.
2. Выполните `git pull`.
3. Соберите новый образ: `docker compose build`.
4. Перезапустите стек: `docker compose up -d`.
5. Проверьте `bash scripts/manual_run.sh status`.
6. При необходимости откройте `bash scripts/manual_run.sh logs`.

## Правила безопасности

- Работайте только как пользователь `pythia`.
- Не запускайте Pythia из каталога `SproutSignal`.
- Не делите сети и тома между проектами.
- Не публикуйте dashboard напрямую наружу. Используйте только loopback-публикацию `127.0.0.1:8787` + reverse proxy.
- Держите все операции внутри compose-проекта `pythia`.
