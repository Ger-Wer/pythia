# Развертывание Pythia на `amper`

Эта инструкция описывает Docker-first развертывание Pythia на хосте `amper` под пользователем `pythia`.

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

Заполните ключи LLM, Telegram и лимиты.

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

Сервис работает без published ports, без общих сетей с другими проектами и без shared volumes.

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
- Не публикуйте порты наружу.
- Держите все операции внутри compose-проекта `pythia`.
