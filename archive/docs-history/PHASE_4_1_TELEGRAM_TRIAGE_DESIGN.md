# Phase 4.1: Telegram Triage Flow — Design Document

**Дата:** 2026-04-13  
**Статус:** Design → Implementation  
**Цель:** Добавить команды управления attention items через Telegram

---

## Архитектура (текущее состояние)

### Backend API (уже готов)
- **Endpoint:** `POST /api/attention-items/{item_id}/transition`
- **Файл:** [`python/dashboard_api.py:208`](../python/dashboard_api.py)
- **Метод:** `core.transition_attention_item(item_id, action)`

### Поддерживаемые actions
```python
# python/attention_store.py:639-650
if action == "start":
    next_status = "in_progress"
elif action == "close":
    next_status = "closed"
elif action == "cancel":
    next_status = "cancelled"
elif action == "bump_today":
    next_priority = "today"
elif action == "bump_later":
    next_priority = "later"
```

### Telegram Bot структура
- **Entry point:** [`python/telegram_operator.py:70`](../python/telegram_operator.py)
- **Command router:** [`python/operator_actions.py:504`](../python/operator_actions.py)
- **Commands list:** [`python/operator_actions.py:24`](../python/operator_actions.py)

---

## Дизайн новых команд

### Mapping: Telegram команды → Backend actions

| Telegram команда | Backend action | Описание |
|------------------|----------------|----------|
| `/done <id>` | `close` | Закрыть attention item (выполнено) |
| `/later <id>` | `bump_later` | Отложить на потом (priority: later) |
| `/urgent <id>` | `bump_today` | Поднять приоритет (priority: today) |
| `/snooze <id>` | `bump_later` | Отложить (алиас для /later) |

### Дополнительные команды

| Telegram команда | Функция | Описание |
|------------------|---------|----------|
| `/inbox` | `list_pending_attention_items()` | Показать pending attention items |
| `/inbox urgent` | `list_pending_attention_items(priority="urgent")` | Только urgent items |
| `/inbox today` | `list_pending_attention_items(priority="today")` | Только today items |

---

## Реализация

### 1. Обновить BOT_COMMANDS

**Файл:** `python/operator_actions.py`

```python
BOT_COMMANDS = [
    ("help", "Список команд и инструкция"),
    ("status", "Сводный статус системы"),
    ("inbox", "Показать pending attention items"),
    ("done", "Закрыть attention item: /done <id>"),
    ("later", "Отложить на потом: /later <id>"),
    ("urgent", "Поднять приоритет: /urgent <id>"),
    ("snooze", "Отложить (алиас /later): /snooze <id>"),
    ("forecast", "Ручной запуск дневного прогона"),
    ("resolve", "Проверка resolved маркетов"),
    ("weekly", "Недельный отчёт и анализ"),
    ("day0", "Быстрая проверка доступности рынков"),
    ("edge", "Проверка Brier score и edge"),
    ("logs", "Последние строки логов"),
    ("health", "Техническое состояние сервисов"),
    ("last_predictions", "Последние прогнозы из базы"),
    ("details", "Подробности по рынку: /details <номер|market_id>"),
]
```

### 2. Обновить COMMAND_ALIASES

```python
COMMAND_ALIASES = {
    # ... existing aliases ...
    "inbox": "inbox",
    "инбокс": "inbox",
    "done": "done",
    "готово": "done",
    "later": "later",
    "позже": "later",
    "urgent": "urgent",
    "срочно": "urgent",
    "snooze": "snooze",
    "отложить": "snooze",
}
```

### 3. Добавить функции в operator_actions.py

```python
def render_inbox(priority: str | None = None) -> str:
    """Показать pending attention items."""
    core = build_operator_core()
    items = core.list_attention_items(priority=priority, status="open")
    
    if not items:
        if priority:
            return f"📭 Нет открытых attention items с приоритетом '{priority}'."
        return "📭 Нет открытых attention items."
    
    lines = ["📬 Открытые attention items:", ""]
    for item in items:
        priority_emoji = {"urgent": "🔴", "today": "🟡", "later": "🔵"}.get(item["priority"], "⚪")
        lines.append(
            f"{priority_emoji} #{item['id']} [{item['priority']}] {item['title']}\n"
            f"   {item['detail']}\n"
            f"   Действия: /done {item['id']} | /later {item['id']} | /urgent {item['id']}"
        )
    
    return "\n".join(lines)


def transition_attention_item_command(item_id_str: str | None, action: str) -> str:
    """Выполнить transition для attention item."""
    if not item_id_str:
        return f"❌ Укажите ID attention item. Пример: /{action} 123"
    
    try:
        item_id = int(item_id_str)
    except ValueError:
        return f"❌ Неверный ID: {item_id_str}. Должно быть число."
    
    core = build_operator_core()
    try:
        result = core.transition_attention_item(item_id, action)
        action_text = {
            "close": "закрыт",
            "bump_later": "отложен на потом",
            "bump_today": "поднят в приоритет",
        }.get(action, action)
        return f"✅ Attention item #{item_id} {action_text}.\n\nСтатус: {result.get('status')}, Приоритет: {result.get('priority')}"
    except LookupError:
        return f"❌ Attention item #{item_id} не найден."
    except Exception as exc:
        return f"❌ Ошибка при обработке: {exc}"
```

### 4. Обновить run_operator_command()

```python
def run_operator_command(command_key: str, command_arg: str | None = None) -> str:
    if command_key == "help":
        return build_help_message()
    if command_key == "inbox":
        # Поддержка /inbox, /inbox urgent, /inbox today
        return render_inbox(priority=command_arg)
    if command_key == "done":
        return transition_attention_item_command(command_arg, "close")
    if command_key == "later":
        return transition_attention_item_command(command_arg, "bump_later")
    if command_key == "urgent":
        return transition_attention_item_command(command_arg, "bump_today")
    if command_key == "snooze":
        return transition_attention_item_command(command_arg, "bump_later")
    if command_key in {"forecast", "resolve", "weekly"}:
        return _run_entrypoint_command(command_key)
    if command_key == "day0":
        return _run_day0_check()
    if command_key == "edge":
        return _run_edge_check()
    if command_key == "logs":
        return render_logs()
    if command_key == "health":
        return render_health()
    if command_key == "status":
        return render_status()
    if command_key == "last_predictions":
        return render_last_predictions()
    if command_key == "details":
        return render_details(command_arg)
    return build_help_message()
```

### 5. Обновить build_help_message()

```python
def build_help_message() -> str:
    return "\n".join(
        [
            "Команды Pythia Operator:",
            "/help или помощь - показать эту справку",
            "/status или статус - сводка по сервисам и базе",
            "",
            "📬 Attention Items:",
            "/inbox или инбокс - показать открытые attention items",
            "/inbox urgent - только срочные",
            "/inbox today - только на сегодня",
            "/done <id> или готово <id> - закрыть item",
            "/later <id> или позже <id> - отложить на потом",
            "/urgent <id> или срочно <id> - поднять приоритет",
            "/snooze <id> или отложить <id> - отложить (алиас /later)",
            "",
            "🔮 Прогнозы:",
            "/forecast или прогноз - вручную запустить forecast",
            "/resolve или резолв - проверить resolved-маркеты",
            "/weekly или недельный отчет - собрать недельную сводку",
            "/day0 или проверка рынка - выполнить day0 check",
            "/edge - посчитать edge и Brier score",
            "/last_predictions или последние - вывести последние прогнозы",
            "/details <номер|market_id> или детали <номер|market_id> - показать полный разбор рынка",
            "",
            "🔧 Система:",
            "/logs или логи - показать последние строки логов",
            "/health или здоровье - показать состояние сервисов",
        ]
    )
```

---

## Тестирование

### Unit tests
1. Проверить `parse_command_request()` с новыми командами
2. Проверить `render_inbox()` с разными приоритетами
3. Проверить `transition_attention_item_command()` с валидными/невалидными ID

### Integration tests
1. Создать test attention item в БД
2. Отправить `/inbox` → проверить, что item отображается
3. Отправить `/done <id>` → проверить, что status = "closed"
4. Отправить `/later <id>` → проверить, что priority = "later"
5. Отправить `/urgent <id>` → проверить, что priority = "today"

### Manual testing (на сервере)
1. SSH на amper
2. Отправить тестовое сообщение в Telegram bot
3. Проверить ответ бота
4. Проверить изменения в БД через dashboard

---

## Deployment checklist

- [ ] Обновить `python/operator_actions.py`
- [ ] Запустить unit tests локально: `pytest python/test_operator_actions.py -v`
- [ ] Закоммитить изменения
- [ ] Push в GitHub
- [ ] SSH на amper
- [ ] Pull изменения
- [ ] Перезапустить telegram-bot контейнер: `docker compose restart pythia-telegram-bot`
- [ ] Проверить логи: `docker compose logs -f pythia-telegram-bot`
- [ ] Отправить тестовую команду `/inbox` в Telegram
- [ ] Проверить dashboard на наличие изменений

---

## Acceptance criteria

✅ Команда `/inbox` показывает список открытых attention items  
✅ Команда `/inbox urgent` фильтрует только urgent items  
✅ Команда `/done <id>` закрывает attention item  
✅ Команда `/later <id>` меняет priority на "later"  
✅ Команда `/urgent <id>` меняет priority на "today"  
✅ Команда `/snooze <id>` работает как алиас для `/later`  
✅ Русские алиасы работают: `/готово`, `/позже`, `/срочно`, `/отложить`  
✅ Команды без аргументов показывают подсказку  
✅ Команды с невалидным ID показывают ошибку  
✅ Help message обновлён с новыми командами  

---

## Следующие шаги (Phase 4.2)

После успешного деплоя Phase 4.1:
- [ ] Phase 4.2: Daily digest (ежедневная сводка attention items в Telegram)
- [ ] Phase 4.3: Proactive notifications (автоматические уведомления о новых urgent items)
