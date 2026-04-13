# PYTHIA ROADMAP - Детальный план следующих этапов

Дата: 2026-04-13
Статус: Phase 3 завершается, переход к Phase 4 и Phase 5

## Текущее состояние

### ✅ Завершено (2026-04-13):
- Phase 0: Production base stabilized
- Phase 1: Decision Journal + Shadow Trading Core
- Phase 2: Autonomous Watchlist Engine (baseline)
- Phase 3A: Command-center foundation
- Phase 3B: WCAG 2.1 AA compliance + UX improvements
- Phase 4A: Attention engine foundation

### 🎯 Следующие приоритеты

## Phase 4 — Attention Engine + Telegram Inbox (NEXT)

### Цель
Завершить attention engine с полным Telegram workflow для эффективной triage.

### Задачи

#### 4.1 Inbound Telegram Triage Flow
**Приоритет:** HIGH
**Оценка:** 2-3 дня

**Требования:**
- Оператор может отвечать на Telegram alerts прямо из чата
- Команды: `/done`, `/later`, `/urgent`, `/snooze 1h`
- Обновление `attention_items` статусов через Telegram
- Синхронизация с dashboard workspace

**Файлы:**
- `python/telegram_operator.py` - добавить command handlers
- `src/services/attention_engine.ts` - API для status updates
- `sql/schema.sql` - возможно новые поля для Telegram message tracking

**Acceptance criteria:**
- [ ] Оператор может закрыть attention item через `/done` в Telegram
- [ ] Оператор может отложить через `/later` или `/snooze`
- [ ] Статус синхронизируется с dashboard в реальном времени
- [ ] История triage actions логируется в `attention_item_events`

#### 4.2 Единый дневной digest
**Приоритет:** MEDIUM
**Оценка:** 1-2 дня

**Требования:**
- Ежедневный summary в 09:00 UTC
- Секции: Urgent (не закрыто), Today (новое), Completed (вчера)
- Краткая статистика: open/closed ratio, avg response time
- Ссылки на dashboard для deep dive

**Файлы:**
- `python/telegram_operator.py` - daily digest generator
- `src/services/attention_engine.ts` - digest data aggregation
- `supercronic.crontab` - schedule для 09:00 UTC

**Acceptance criteria:**
- [ ] Digest приходит каждый день в 09:00 UTC
- [ ] Показывает актуальную attention queue
- [ ] Включает статистику за последние 24h
- [ ] Форматирование читаемое и структурированное

#### 4.3 Smart notification throttling
**Приоритет:** LOW
**Оценка:** 1 день

**Требования:**
- Не спамить оператора если он уже в dashboard
- Группировать похожие alerts (например, несколько watch updates)
- Rate limiting: max 5 urgent alerts в час

**Файлы:**
- `python/telegram_operator.py` - throttling logic
- `src/services/attention_engine.ts` - grouping heuristics

**Acceptance criteria:**
- [ ] Alerts группируются если приходят в течение 5 минут
- [ ] Не больше 5 urgent notifications в час
- [ ] Оператор может настроить quiet hours

---

## Phase 5 — Decision Quality Layer (CRITICAL)

### Цель
Построить metrics и dashboard для валидации paper/shadow track record и улучшения decision-making.

### Задачи

#### 5.1 Shadow Performance Dashboard
**Приоритет:** HIGH
**Оценка:** 3-4 дня

**Требования:**
- Новая страница `/quality/shadow-performance`
- Метрики:
  - Win rate (shadow positions)
  - Average P&L per position
  - Sharpe ratio (если достаточно данных)
  - Best/worst performers
- Фильтры: по датам, по типу signal, по market category
- Визуализация: equity curve, win/loss distribution

**Файлы:**
- `dashboard/src/pages/ShadowPerformancePage.tsx` - новая страница
- `src/services/decision_core.ts` - aggregation queries
- `dashboard/src/components/PerformanceChart.tsx` - equity curve
- `dashboard/src/components/PerformanceMetrics.tsx` - KPI cards

**SQL queries:**
```sql
-- Win rate
SELECT 
  COUNT(CASE WHEN final_pnl > 0 THEN 1 END) * 100.0 / COUNT(*) as win_rate
FROM positions 
WHERE status = 'closed' AND mode = 'shadow';

-- Average P&L
SELECT AVG(final_pnl) as avg_pnl
FROM positions
WHERE status = 'closed' AND mode = 'shadow';

-- Equity curve
SELECT 
  closed_at,
  SUM(final_pnl) OVER (ORDER BY closed_at) as cumulative_pnl
FROM positions
WHERE status = 'closed' AND mode = 'shadow'
ORDER BY closed_at;
```

**Acceptance criteria:**
- [ ] Dashboard показывает win rate и avg P&L
- [ ] Equity curve визуализирует cumulative performance
- [ ] Можно фильтровать по датам и категориям
- [ ] Данные обновляются при каждом закрытии position

#### 5.2 Operator vs Shadow Comparison
**Приоритет:** HIGH
**Оценка:** 2-3 дня

**Требования:**
- Side-by-side comparison: operator decisions vs shadow
- Метрики:
  - Agreement rate (когда оператор согласен с shadow)
  - Divergence analysis (когда не согласен и почему)
  - Performance delta (кто лучше)
- Highlight cases где оператор outperformed shadow (learning opportunities)

**Файлы:**
- `dashboard/src/pages/OperatorVsShadowPage.tsx` - новая страница
- `src/services/decision_core.ts` - comparison queries
- `dashboard/src/components/DecisionComparisonTable.tsx` - таблица

**SQL queries:**
```sql
-- Agreement rate
SELECT 
  COUNT(CASE WHEN d.action = 'accept' THEN 1 END) * 100.0 / COUNT(*) as agreement_rate
FROM decisions d
JOIN signals s ON d.signal_id = s.id
WHERE d.mode = 'operator';

-- Performance delta
SELECT 
  AVG(CASE WHEN p.mode = 'operator' THEN p.final_pnl END) as operator_avg,
  AVG(CASE WHEN p.mode = 'shadow' THEN p.final_pnl END) as shadow_avg,
  AVG(CASE WHEN p.mode = 'operator' THEN p.final_pnl END) - 
  AVG(CASE WHEN p.mode = 'shadow' THEN p.final_pnl END) as delta
FROM positions p
WHERE p.status = 'closed';
```

**Acceptance criteria:**
- [ ] Показывает agreement rate между operator и shadow
- [ ] Визуализирует performance delta
- [ ] Highlight интересные divergence cases
- [ ] Можно drill down в конкретные decisions

#### 5.3 Review Discipline Metrics
**Приоритет:** MEDIUM
**Оценка:** 2 дня

**Требования:**
- Метрики дисциплины:
  - Review completion rate
  - Average time to review
  - Overdue reviews count
  - Post-mortem completion rate
- Dashboard widget на Overview page
- Alerts если discipline падает

**Файлы:**
- `dashboard/src/components/DisciplineMetrics.tsx` - widget
- `src/services/decision_core.ts` - discipline queries
- `dashboard/src/pages/OverviewPage.tsx` - добавить widget

**SQL queries:**
```sql
-- Review completion rate
SELECT 
  COUNT(CASE WHEN r.status = 'completed' THEN 1 END) * 100.0 / COUNT(*) as completion_rate
FROM reviews r;

-- Average time to review
SELECT 
  AVG(julianday(r.completed_at) - julianday(r.created_at)) as avg_days
FROM reviews r
WHERE r.status = 'completed';

-- Overdue reviews
SELECT COUNT(*) as overdue_count
FROM reviews r
WHERE r.status = 'pending' 
  AND julianday('now') > julianday(r.deadline);
```

**Acceptance criteria:**
- [ ] Widget показывает review completion rate
- [ ] Показывает overdue reviews count
- [ ] Alert если completion rate < 80%
- [ ] Можно кликнуть и перейти к overdue reviews

#### 5.4 Weekly Decision Review
**Приоритет:** MEDIUM
**Оценка:** 2-3 дня

**Требования:**
- Еженедельный review session (каждый понедельник 10:00 UTC)
- Автоматический report:
  - Все decisions за неделю
  - Performance summary
  - Lessons learned (из post-mortems)
  - Action items для следующей недели
- Сохранение в `weekly_reviews` таблицу
- Telegram notification с summary

**Файлы:**
- `python/weekly_review.py` - новый модуль
- `sql/schema.sql` - таблица `weekly_reviews`
- `src/services/decision_core.ts` - weekly aggregation
- `supercronic.crontab` - schedule для понедельника 10:00

**SQL schema:**
```sql
CREATE TABLE weekly_reviews (
  id TEXT PRIMARY KEY,
  week_start DATE NOT NULL,
  week_end DATE NOT NULL,
  total_decisions INTEGER,
  total_positions INTEGER,
  win_rate REAL,
  total_pnl REAL,
  lessons_learned TEXT,
  action_items TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**Acceptance criteria:**
- [ ] Review генерируется каждый понедельник
- [ ] Включает все decisions за прошлую неделю
- [ ] Показывает performance metrics
- [ ] Извлекает lessons learned из post-mortems
- [ ] Отправляется в Telegram

---

## Phase 6 — Adaptive Improvement Layer (FUTURE)

### Цель
Использовать накопленный dataset для улучшения ranking, prompts, heuristics.

### Задачи (high-level)

#### 6.1 Signal Ranking Improvement
- Анализ historical signal performance
- Обучение ranking model на реальных outcomes
- A/B testing новых ranking heuristics

#### 6.2 Prompt Engineering Loop
- Сбор feedback на качество LLM analysis
- Systematic prompt improvements
- Validation на historical data

#### 6.3 Decision Heuristics Tuning
- Анализ operator overrides
- Выявление patterns в successful decisions
- Codification лучших practices

---

## Приоритизация задач (Next 2 weeks)

### Week 1 (2026-04-14 → 2026-04-20)
1. **Phase 4.1**: Inbound Telegram Triage Flow (HIGH)
2. **Phase 5.1**: Shadow Performance Dashboard (HIGH)
3. **Phase 4.2**: Единый дневной digest (MEDIUM)

### Week 2 (2026-04-21 → 2026-04-27)
1. **Phase 5.2**: Operator vs Shadow Comparison (HIGH)
2. **Phase 5.3**: Review Discipline Metrics (MEDIUM)
3. **Phase 5.4**: Weekly Decision Review (MEDIUM)

### Week 3+ (2026-04-28+)
1. **Phase 4.3**: Smart notification throttling (LOW)
2. **Phase 6**: Начать планирование Adaptive Improvement Layer

---

## Метрики успеха

### Phase 4 (Attention Engine)
- [ ] 90%+ attention items закрываются через Telegram
- [ ] Оператор тратит <5 минут на daily triage
- [ ] Notification noise снижен на 50%

### Phase 5 (Decision Quality)
- [ ] Shadow track record visible и trackable
- [ ] Operator vs shadow comparison доступен
- [ ] Review discipline >80% completion rate
- [ ] Weekly reviews генерируются автоматически

---

## Риски и зависимости

### Технические риски
- SQLite performance с большим объемом positions (mitigation: индексы, архивация старых данных)
- Telegram API rate limits (mitigation: throttling, batching)
- Dashboard performance с большими datasets (mitigation: pagination, lazy loading)

### Продуктовые риски
- Оператор может игнорировать metrics (mitigation: сделать actionable, не просто цифры)
- Слишком много notifications (mitigation: smart throttling, digest)
- Complexity creep (mitigation: MVP first, iterate)

---

## Следующие шаги

1. ✅ Обновить PYTHIA_V2_WORKSTREAM.md
2. ⏳ Создать GitHub issues для Phase 4.1, 5.1, 4.2
3. ⏳ Начать с Phase 4.1 (Inbound Telegram Triage)
4. ⏳ Параллельно работать над Phase 5.1 (Shadow Performance Dashboard)
