# Pythia Dashboard - UX Analysis & Redesign Plan
## 2026-04-12 12:21 UTC

## Цель
Сделать дашборд максимально user-friendly, чтобы новый оператор мог начать работу через 5 минут после первого входа.

## Текущие проблемы (Critical UX Issues)

### 1. Cognitive Overload - Слишком много информации сразу
**Проблема:**
- OverviewPage показывает 8+ секций одновременно
- WatchDossierCard содержит 7-8 строк метаданных
- Нет визуальной иерархии "что важно СЕЙЧАС"

**Решение:**
- Progressive disclosure: показывать только критичное, остальное - по клику
- Визуальная иерархия: размер шрифта = важность
- Цветовое кодирование: красный = срочно, жёлтый = сегодня, зелёный = ок

### 2. Unclear Mental Model - Непонятно что делать
**Проблема:**
- Нет онбординга для нового оператора
- Термины не объяснены (thesis state, attention priority, shadow vs operator)
- Не понятен workflow: с чего начать, что дальше

**Решение:**
- First-time user tour (react-joyride)
- Inline tooltips для всех терминов
- Workflow wizard: "Начните с проверки срочных задач → Затем посмотрите новые сигналы → Обновите watchlist"

### 3. Too Many Clicks - Слишком много кликов
**Проблема:**
- Чтобы открыть позицию: Overview → Market Detail → Scroll → Fill form → Submit (5 шагов)
- Чтобы закрыть attention item: Watchlist → Find card → Click button → Confirm (4 шага)

**Решение:**
- Quick actions в карточках (inline buttons)
- Keyboard shortcuts (Enter = primary action, Esc = cancel)
- Bulk actions (выбрать несколько → действие)

### 4. Poor Scannability - Плохо сканируется
**Проблема:**
- Все тексты одного размера
- Нет иконок для быстрого распознавания
- Метаданные смешаны с основным контентом

**Решение:**
- Иконки для статусов (⚠️ urgent, 📅 today, 👁️ watch, ✅ ok)
- Крупнее заголовки, мельче метаданные
- Группировка: thesis + prices в один блок, actions в другой

### 5. No Feedback - Нет обратной связи
**Проблема:**
- После действия нет подтверждения (кроме перезагрузки данных)
- Loading states отсутствуют
- Ошибки показываются как обычный текст

**Решение:**
- Toast notifications (react-hot-toast)
- Skeleton screens при загрузке
- Optimistic UI (показать результат сразу, откатить если ошибка)

### 6. Mobile Unfriendly - Не адаптирован для мобилки
**Проблема:**
- Sidebar 260px на мобилке съедает половину экрана
- Таблицы требуют горизонтального скролла
- Кнопки в карточках коллапсируют в столбец

**Решение:**
- Collapsible sidebar (hamburger menu)
- Responsive cards вместо таблиц
- Bottom sheet для actions на мобилке

## Redesign Plan - Пошаговый план улучшений

### Phase 1: Quick Wins (2-3 часа)
**Цель:** Улучшить визуальную иерархию и feedback

1. **Visual Hierarchy**
   - Увеличить заголовки карточек (1.1rem → 1.25rem)
   - Уменьшить метаданные (1rem → 0.88rem)
   - Добавить font-weight: 700 для важных значений

2. **Icons & Status**
   - Добавить иконки для статусов
   - Цветовое кодирование: urgent = красный фон, today = жёлтый, later = серый

3. **Loading States**
   - Skeleton screens для карточек
   - Spinner для кнопок (disabled + loading)

4. **Toast Notifications**
   - Установить react-hot-toast
   - Показывать успех/ошибку после действий

### Phase 2: Onboarding (4-5 часов)
**Цель:** Помочь новому оператору понять систему

1. **First-Time Tour**
   - Установить react-joyride
   - 5-шаговый тур: Overview → Watchlist → Signals → Market Detail → Operations

2. **Inline Help**
   - Tooltips для всех терминов (thesis state, attention priority, etc)
   - "?" иконки рядом с непонятными концепциями
   - Expandable "Что это значит?" секции

3. **Empty States**
   - Иллюстрации + текст + CTA
   - "Нет срочных задач? Отлично! Проверьте новые сигналы"
   - "Watchlist пуст? Добавьте первый рынок из сигналов"

### Phase 3: Workflow Optimization (6-8 часов)
**Цель:** Уменьшить количество кликов

1. **Quick Actions**
   - Inline buttons в карточках (не переходить на другую страницу)
   - Modal для быстрых действий (открыть позицию, закрыть attention)
   - Bulk actions (выбрать несколько → закрыть все)

2. **Keyboard Shortcuts**
   - "/" = search
   - "1-7" = навигация по страницам
   - "Enter" = primary action
   - "Esc" = cancel/close
   - "?" = показать все shortcuts

3. **Smart Defaults**
   - Pre-fill forms на основе контекста
   - Remember last used values
   - Auto-focus на первое поле

### Phase 4: Information Architecture (8-10 часов)
**Цель:** Упростить структуру информации

1. **Progressive Disclosure**
   - Показывать только критичное по умолчанию
   - "Показать детали" для метаданных
   - Collapsible sections для длинных текстов

2. **Simplified Cards**
   - WatchDossierCard: 3 строки вместо 8
   - Основное: заголовок + статус + primary action
   - Детали: по клику "Показать больше"

3. **Dashboard Redesign**
   - Overview: только "Что делать СЕЙЧАС" + срочные задачи
   - Остальное: tabs или collapsible sections
   - Metrics: в отдельную страницу (не нужны каждый день)

### Phase 5: Advanced Features (10-12 часов)
**Цель:** Сделать работу ещё быстрее

1. **Command Palette**
   - Cmd+K = открыть command palette
   - Быстрый поиск рынков, действий, страниц
   - Fuzzy search

2. **Notifications Center**
   - Bell icon в header
   - Список всех attention items
   - Mark as read/done

3. **Customization**
   - Настройки: что показывать на Overview
   - Сохранять в localStorage
   - Export/import настроек

## Конкретные изменения по компонентам

### OverviewPage.tsx
**Было:** 8 секций, все видны сразу
**Станет:**
```
1. Hero: "Что делать СЕЙЧАС" (крупно, ярко)
2. Срочные задачи (если есть) - красные карточки
3. Сегодня (если есть) - жёлтые карточки
4. [Collapsible] Активные позиции
5. [Collapsible] Последние изменения
6. [Collapsible] Метрики системы
```

### WatchDossierCard.tsx
**Было:** 7-8 строк метаданных
**Станет:**
```
[Заголовок] Market question (1.25rem, bold)
[Статус] 🔴 Срочно / 🟡 Сегодня / 🟢 Наблюдение
[Тезис] Current thesis summary (1 строка)
[Action] Primary button (крупно, ярко)
[Детали] "Показать больше" → expandable section
```

### KpiCard.tsx
**Было:** Статичные карточки
**Станет:**
```
[Значение] 42 (2.5rem, bold)
[Тренд] ↑ +5 за 24ч (зелёный если рост)
[Label] Прогнозов в базе
[Sparkline] Мини-график за неделю
```

### Navigation
**Было:** Sidebar 260px, всегда видна
**Станет:**
```
Desktop: Collapsible sidebar (иконки + текст → только иконки)
Mobile: Hamburger menu + bottom navigation
Keyboard: 1-7 для быстрой навигации
```

## Metrics для оценки успеха

1. **Time to First Action** - сколько времени новый оператор тратит до первого действия
   - Цель: < 5 минут (сейчас ~15-20 минут)

2. **Clicks per Task** - сколько кликов нужно для типичной задачи
   - Цель: < 3 клика (сейчас 5-7 кликов)

3. **Error Rate** - как часто оператор делает ошибки
   - Цель: < 5% (сейчас ~15-20%)

4. **User Satisfaction** - субъективная оценка
   - Цель: 8/10 (сейчас ~5/10)

## Next Steps

1. ✅ Анализ текущего состояния (done)
2. 🔄 Phase 1: Quick Wins (starting now)
3. ⏳ Phase 2: Onboarding
4. ⏳ Phase 3: Workflow Optimization
5. ⏳ Phase 4: Information Architecture
6. ⏳ Phase 5: Advanced Features

---

**Создано:** 2026-04-12 12:21 UTC
**Статус:** Analysis complete, ready to implement
**Приоритет:** Phase 1 (Quick Wins) - начинаем сейчас
