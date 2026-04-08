# Аудит Pythia

Дата актуализации: 2026-04-08

## 1. Краткий вывод

Pythia сейчас - это рабочая Docker-first система прогнозной аналитики и сопровождения тезисов для Polymarket.

В production уже есть:

- daily forecast;
- `Research+R1` и `Naked R1`;
- `decision core` для paper/shadow lifecycle;
- `autonomous watchlist engine`;
- dashboard как основной интерфейс;
- Telegram как канал alerts и fallback-команд.

В production пока нет:

- live trading;
- хранения реальных trading-ключей;
- полноценного `attention engine`.

## 2. Назначение системы

Pythia помогает оператору:

- находить новые сильные сигналы;
- сопровождать выбранные рынки;
- вести paper/shadow decisions;
- копить decision dataset для дальнейшего улучшения.

Система не пытается быть автотрейдером.

## 3. Runtime-топология

Хост:

- `amper`

Контейнеры:

- `pythia-scheduler`
- `pythia-telegram`
- `pythia-dashboard`

Свойства размещения:

- пользователь `pythia`;
- каталог `/home/pythia/pythia`;
- bind-mounted `data/` и `logs/`;
- dashboard доступен только через `127.0.0.1:8787` + reverse proxy.

## 4. Главные runtime-контуры

### Forecast stack

Отвечает за:

- выбор рынков;
- research;
- synthesis;
- создание прогнозов;
- создание первичных `signals`.

### Decision core

Отвечает за:

- `signals`
- `decisions`
- `positions`
- `reviews`
- `postmortems`

Это слой paper/shadow lifecycle.

### Autonomous watchlist engine

Отвечает за:

- `watch dossiers`
- `watchlist refresh`
- monitoring mode
- deep review по триггерам
- `watch updates`
- Telegram delivery для `watch updates`

### Operator layer

Отвечает за:

- dashboard;
- whitelisted operator actions;
- Telegram operator/fallback;
- read-model для оператора.

## 5. Что реально работает в production

### Ежедневный прогнозный цикл

Есть:

- ступенчатый отбор рынков до горизонта `60` дней;
- дедупликация по событию;
- diversity constraints;
- multi-search research;
- `Research+R1` и `Naked R1`;
- сохранение прогнозов в SQLite.

### Paper/shadow lifecycle

Есть:

- signal generation;
- shadow decisions;
- shadow positions;
- manual operator decisions;
- reviews;
- postmortems.

### Watchlist follow-up

Есть:

- dossier bootstrap при добавлении в `Избранное`;
- отдельный `watchlist refresh` каждые 6 часов;
- дешёвая monitoring-проверка;
- deep review по триггерам;
- operator-facing `watch updates`;
- доставка `watch updates` в Telegram.

### Dashboard

Есть:

- `Сводка`
- `Качество`
- `Сигналы`
- dossier-first `Разбор рынка`
- `Избранное` как board по `watch dossiers`
- `Действия`

## 6. Что еще не завершено

Следующие части еще находятся в roadmap:

- `attention_item` как единая сущность внимания;
- Telegram triage inbox;
- `Сводка` как полноценный command center;
- decision-quality метрики поверх `operator vs shadow`;
- более сильный command workflow вокруг `Избранного`.

## 7. Роль LLM и news API

LLM в текущем production не является "магическим источником alpha".

Он используется в двух местах:

- в forecast-контуре для research и synthesis;
- в watchlist-контуре для monitoring/deep-review логики.

News API используются для поиска новых значимых изменений, а не для вывода dump-списков ссылок оператору.

## 8. Telegram и dashboard

Текущее разделение ролей такое:

- dashboard - главное рабочее место оператора;
- Telegram - alerts, `watch updates`, fallback-команды.

Это уже реализовано в production и соответствует текущей продуктовой модели.

## 9. Ограничения и честные оговорки

- live execution нет;
- реальных trading-ключей в системе нет;
- `attention engine` еще не построен;
- operator UX еще не доведен до финальной command-center формы;
- quality layer по решениям слабее quality layer по прогнозам.

## 10. Текущий статус по качеству продукта

По архитектуре система уже перешла из состояния "умный forecast pipeline" в состояние "forecast + decision + watchlist follow-up".

По продукту текущий разрыв такой:

- back-end контуры уже сильнее, чем UX;
- операторский workflow еще можно заметно упростить;
- следующий главный шаг - превратить `Сводку` и `Избранное` в настоящий command workflow.

## 11. Source of truth

Если нужен текущий ответ "что есть в проде, а что еще нет", опирайтесь в таком порядке:

1. [docs/PYTHIA_V2_WORKSTREAM.md](/Users/gerwer/Desktop/Pythia/docs/PYTHIA_V2_WORKSTREAM.md)
2. [README.md](/Users/gerwer/Desktop/Pythia/README.md)
3. [docs/ARCHITECTURE.md](/Users/gerwer/Desktop/Pythia/docs/ARCHITECTURE.md)
4. этот документ
5. исторические spec-docs

## 12. Вывод для внешнего аудитора

Pythia на 2026-04-08 - это production-ready Docker-first decision-support система для Polymarket с paper/shadow lifecycle и отдельным watchlist follow-up контуром.

Она уже пригодна для controlled paper-validation и дальнейшего улучшения signal quality, review logic и operator workflow.

Она еще не является live-trading системой и не должна аудироваться как автотрейдер.
