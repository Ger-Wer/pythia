# NotebookLM-py Research Report
## 2026-04-12

## Что это?

**notebooklm-py** - неофициальный Python API и skill для Google NotebookLM с полным программным доступом ко всем функциям, включая те, которые недоступны в веб-интерфейсе.

**Репозиторий:** https://github.com/teng-lin/notebooklm-py  
**Звезды:** 10,221  
**Язык:** Python  
**Лицензия:** MIT

## Ключевые возможности

### 1. Три способа использования
- **Python API** - для интеграции в приложения
- **CLI** - для скриптов и автоматизации
- **Agent Integration** - для Claude Code, Codex и других LLM агентов

### 2. Полный доступ к NotebookLM
- Управление ноутбуками (создание, список, переименование, удаление)
- Источники: URLs, YouTube, PDF, текст, Markdown, Word, аудио, видео, изображения, Google Drive
- Чат с документами
- Web и Drive research агенты (fast/deep режимы)
- Sharing и permissions

### 3. Генерация контента (все типы NotebookLM Studio)
- **Audio Overview** (подкасты) - 4 формата, 3 длины, 50+ языков → MP3/MP4
- **Video Overview** - 3 формата, 9 визуальных стилей → MP4
- **Slide Deck** - детальный/презентационный формат → PDF, PPTX
- **Infographic** - 3 ориентации, 3 уровня детализации → PNG
- **Quiz** - настраиваемое количество и сложность → JSON, Markdown, HTML
- **Flashcards** - настраиваемое количество и сложность → JSON, Markdown, HTML
- **Report** - briefing doc, study guide, blog post → Markdown
- **Data Table** - структура через natural language → CSV
- **Mind Map** - интерактивная визуализация → JSON

### 4. Возможности за пределами веб-UI
- Batch downloads всех артефактов
- Quiz/Flashcard export в структурированных форматах
- Mind map data extraction (JSON)
- Data table CSV export
- Slide deck как PPTX (веб-UI только PDF)
- Slide revision (модификация отдельных слайдов)
- Report template customization
- Save chat to notes
- Source fulltext access
- Programmatic sharing

## Архитектура

```
CLI Layer (cli/)
    ↓
Client Layer (client.py, _*.py APIs)
    ↓
Core Layer (_core.py)
    ↓
RPC Layer (rpc/)
```

**Критическое ограничение:** Использует недокументированные Google RPC APIs (`batchexecute` протокол) с обфусцированными method IDs, которые Google может изменить в любой момент.

## Установка и использование

### Установка
```bash
pip install notebooklm-py
pip install "notebooklm-py[browser]"  # с поддержкой браузера для логина
playwright install chromium
```

### Skill для Claude Code
```bash
# Вариант 1 - CLI install
notebooklm skill install

# Вариант 2 - npx install
npx skills add teng-lin/notebooklm-py
```

### Аутентификация (обязательно!)
```bash
notebooklm login          # Открывает браузер для Google OAuth
notebooklm list           # Проверка аутентификации
```

### Базовый workflow
```bash
# 1. Создать ноутбук и добавить источники
notebooklm create "My Research"
notebooklm use <notebook_id>
notebooklm source add "https://example.com"
notebooklm source add "./paper.pdf"

# 2. Чат с источниками
notebooklm ask "What are the key themes?"

# 3. Генерация контента
notebooklm generate audio "make it engaging" --wait
notebooklm generate video --style whiteboard --wait
notebooklm generate quiz --difficulty hard

# 4. Скачивание
notebooklm download audio ./podcast.mp3
notebooklm download quiz --format markdown ./quiz.md
```

## Применение для исследований

### Автоматизация исследований
- Bulk-import источников (URLs, PDFs, YouTube, Google Drive)
- Web/Drive research queries с auto-import
- Программное извлечение insights
- Повторяемые research pipelines

### Генерация контента
- Подкасты из документов
- Видео-обзоры
- Квизы и флешкарты для обучения
- Инфографика
- Mind maps
- Study guides

### Экспорт и интеграция
- Скачивание всех артефактов локально
- Экспорт в Google Docs/Sheets
- Batch downloads
- Структурированные форматы (JSON, Markdown, CSV)

## Как это может помочь Claude Code

### 1. Исследование проектов
**Проблема:** Сейчас я зависаю при попытке исследовать большие репозитории (как с Kalshi bot).

**Решение с NotebookLM:**
1. Загрузить README, ключевые файлы, документацию в NotebookLM
2. Использовать `notebooklm ask` для получения структурированных ответов
3. Генерировать study guides и mind maps для понимания архитектуры
4. Создавать подкасты для объяснения сложных концепций

### 2. Анализ документации
- Загрузить всю документацию проекта
- Задавать вопросы через chat API
- Получать source-grounded ответы с цитатами
- Генерировать summaries и briefing docs

### 3. Обучение и knowledge base
- Создавать flashcards из технической документации
- Генерировать quizzes для проверки понимания
- Mind maps для визуализации связей
- Audio overviews для быстрого ознакомления

### 4. Автоматизация workflow
```python
# Пример: автоматическое исследование нового проекта
async def research_project(repo_url):
    async with await NotebookLMClient.from_storage() as client:
        # Создать ноутбук
        nb = await client.notebooks.create(f"Research: {repo_url}")
        
        # Добавить источники
        await client.sources.add_url(nb.id, f"{repo_url}/README.md")
        await client.sources.add_url(nb.id, f"{repo_url}/docs")
        
        # Получить summary
        result = await client.chat.ask(nb.id, 
            "Summarize the architecture, key features, and tech stack")
        
        # Сгенерировать study guide
        await client.artifacts.generate_report(nb.id, 
            format="study-guide")
        
        # Сгенерировать mind map
        await client.artifacts.generate_mind_map(nb.id)
        
        return result.answer
```

## Ограничения и риски

### 1. Неофициальный API
- Использует недокументированные Google APIs
- Может сломаться в любой момент
- Google может изменить internal endpoints
- Rate limits применяются

### 2. Зависимость от Google
- Требует Google аккаунт
- Требует аутентификацию через браузер
- Зависит от доступности NotebookLM

### 3. Технические ограничения
- RPC method IDs обфусцированы
- Может быть rate limiting при bulk операциях
- Artifact generation может падать
- CSRF tokens истекают

## Рекомендации

### ✅ Стоит использовать для:
1. **Исследования больших проектов** - загрузить документацию и задавать вопросы
2. **Создание knowledge base** - структурированное хранение информации о проектах
3. **Генерация учебных материалов** - quizzes, flashcards, study guides
4. **Автоматизация research workflows** - программное добавление источников и извлечение insights
5. **Визуализация архитектуры** - mind maps для понимания структуры проектов

### ⚠️ Не стоит использовать для:
1. **Production-critical систем** - API может сломаться
2. **Real-time операций** - есть rate limits
3. **Конфиденциальных данных** - данные идут в Google
4. **Критичных по времени задач** - generation может быть медленным

## План интеграции

### Шаг 1: Установка и настройка
```bash
pip install "notebooklm-py[browser]"
playwright install chromium
notebooklm login
notebooklm skill install
```

### Шаг 2: Тестирование базового workflow
```bash
# Создать тестовый ноутбук
notebooklm create "Test Research"
notebooklm source add "https://github.com/Ger-Wer/pythia"
notebooklm ask "What is this project about?"
```

### Шаг 3: Интеграция в workflow
- Использовать для исследования новых проектов
- Создавать knowledge base для Pythia
- Генерировать документацию и study guides
- Автоматизировать research tasks

### Шаг 4: Создание helper scripts
```python
# research_helper.py - автоматизация исследований
# knowledge_base.py - управление базой знаний
# doc_generator.py - генерация документации
```

## Выводы

**NotebookLM-py - мощный инструмент для автоматизации исследований и работы с документацией.**

**Главные преимущества:**
- Полный программный доступ к NotebookLM
- Skill для Claude Code (легкая интеграция)
- Генерация разнообразного контента
- Возможности за пределами веб-UI

**Главные риски:**
- Неофициальный API (может сломаться)
- Зависимость от Google
- Rate limits

**Рекомендация:** ✅ **Установить и использовать для исследований**, но не полагаться на него для критичных операций.

---

**Статус:** Готов к установке и тестированию  
**Приоритет:** Высокий (решает проблему зависания при исследованиях)  
**Следующий шаг:** Установить и протестировать базовый workflow
