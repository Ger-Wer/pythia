# Phase 5.2: Operator vs Shadow Comparison - Design Document

**Дата:** 2026-04-13  
**Статус:** Design  
**Приоритет:** HIGH  

## Цель

Создать dashboard для сравнения решений оператора и shadow режима, чтобы:
1. Понять agreement rate между operator и shadow
2. Выявить случаи divergence и их причины
3. Сравнить performance (кто лучше)
4. Найти learning opportunities (где оператор outperformed shadow)

## Архитектура данных

### Ключевые концепции

**Signal** → **Decision** → **Position**

- **Signal**: Генерируется системой для каждого market
- **Decision**: Оператор или shadow принимает решение (accept/reject/wait)
- **Position**: Если decision = accept, открывается позиция
- **Mode**: `shadow` или `operator` (в таблицах `books` и `positions`)

### Типы сравнений

1. **Agreement Analysis**: Когда оператор и shadow получают один signal, как они реагируют?
2. **Performance Comparison**: У кого лучше P&L на закрытых позициях?
3. **Divergence Cases**: Когда оператор принял решение отличное от shadow?

## API Structure

### Endpoint: GET /api/quality/operator-vs-shadow

**Query Parameters:**
- `from_date` (optional): ISO date string
- `to_date` (optional): ISO date string

**Response:**
```typescript
{
  "summary": {
    "total_signals": number,
    "operator_decisions": number,
    "shadow_decisions": number,
    "agreement_rate": number,  // % когда оба согласны
    "operator_performance": {
      "total_positions": number,
      "closed_positions": number,
      "win_rate": number,
      "avg_pnl_usd": number,
      "total_pnl_usd": number
    },
    "shadow_performance": {
      "total_positions": number,
      "closed_positions": number,
      "win_rate": number,
      "avg_pnl_usd": number,
      "total_pnl_usd": number
    },
    "performance_delta": {
      "win_rate_delta": number,  // operator - shadow
      "avg_pnl_delta": number,
      "total_pnl_delta": number
    }
  },
  "divergence_cases": [
    {
      "signal_id": number,
      "market_id": string,
      "question": string,
      "signal_created_at": string,
      "operator_decision": "accept" | "reject" | "wait" | null,
      "shadow_decision": "accept" | "reject" | "wait" | null,
      "operator_position_id": number | null,
      "shadow_position_id": number | null,
      "operator_pnl_usd": number | null,
      "shadow_pnl_usd": number | null,
      "outcome": "operator_better" | "shadow_better" | "both_closed" | "pending"
    }
  ],
  "comparison_chart": [
    {
      "date": string,
      "operator_cumulative_pnl": number,
      "shadow_cumulative_pnl": number
    }
  ]
}
```

## SQL Queries

### 1. Agreement Rate

```sql
-- Подсчёт signals, где есть decisions от обоих
WITH signal_decisions AS (
  SELECT 
    s.id as signal_id,
    s.market_id,
    s.created_at,
    MAX(CASE WHEN b.mode = 'operator' THEN d.decision_type END) as operator_decision,
    MAX(CASE WHEN b.mode = 'shadow' THEN d.decision_type END) as shadow_decision
  FROM signals s
  LEFT JOIN decisions d ON s.id = d.signal_id
  LEFT JOIN books b ON d.book_id = b.id
  WHERE s.created_at >= ? AND s.created_at <= ?
  GROUP BY s.id
)
SELECT 
  COUNT(*) as total_signals,
  COUNT(CASE WHEN operator_decision IS NOT NULL THEN 1 END) as operator_decisions,
  COUNT(CASE WHEN shadow_decision IS NOT NULL THEN 1 END) as shadow_decisions,
  COUNT(CASE 
    WHEN operator_decision IS NOT NULL 
    AND shadow_decision IS NOT NULL 
    AND operator_decision = shadow_decision 
    THEN 1 
  END) * 100.0 / NULLIF(COUNT(CASE 
    WHEN operator_decision IS NOT NULL 
    AND shadow_decision IS NOT NULL 
    THEN 1 
  END), 0) as agreement_rate
FROM signal_decisions;
```

### 2. Performance Comparison

```sql
-- Operator performance
SELECT 
  COUNT(*) as total_positions,
  COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_positions,
  CAST(COUNT(CASE WHEN status = 'closed' AND pnl_usd > 0 THEN 1 END) AS REAL) * 100.0 / 
    NULLIF(COUNT(CASE WHEN status = 'closed' THEN 1 END), 0) as win_rate,
  AVG(CASE WHEN status = 'closed' THEN pnl_usd END) as avg_pnl_usd,
  SUM(CASE WHEN status = 'closed' THEN pnl_usd END) as total_pnl_usd
FROM positions
WHERE mode = 'operator'
  AND opened_at >= ? AND opened_at <= ?;

-- Shadow performance (аналогично с mode = 'shadow')
```

### 3. Divergence Cases

```sql
-- Найти signals где operator и shadow приняли разные решения
WITH signal_decisions AS (
  SELECT 
    s.id as signal_id,
    s.market_id,
    s.question,
    s.created_at as signal_created_at,
    MAX(CASE WHEN b.mode = 'operator' THEN d.decision_type END) as operator_decision,
    MAX(CASE WHEN b.mode = 'shadow' THEN d.decision_type END) as shadow_decision,
    MAX(CASE WHEN b.mode = 'operator' THEN d.id END) as operator_decision_id,
    MAX(CASE WHEN b.mode = 'shadow' THEN d.id END) as shadow_decision_id
  FROM signals s
  LEFT JOIN decisions d ON s.id = d.signal_id
  LEFT JOIN books b ON d.book_id = b.id
  WHERE s.created_at >= ? AND s.created_at <= ?
  GROUP BY s.id
)
SELECT 
  sd.*,
  op.id as operator_position_id,
  op.pnl_usd as operator_pnl_usd,
  op.status as operator_status,
  sp.id as shadow_position_id,
  sp.pnl_usd as shadow_pnl_usd,
  sp.status as shadow_status,
  CASE
    WHEN op.status = 'closed' AND sp.status = 'closed' THEN
      CASE 
        WHEN op.pnl_usd > sp.pnl_usd THEN 'operator_better'
        WHEN sp.pnl_usd > op.pnl_usd THEN 'shadow_better'
        ELSE 'equal'
      END
    WHEN op.status = 'closed' OR sp.status = 'closed' THEN 'partial_closed'
    ELSE 'pending'
  END as outcome
FROM signal_decisions sd
LEFT JOIN positions op ON sd.operator_decision_id = op.opened_by_decision_id
LEFT JOIN positions sp ON sd.shadow_decision_id = sp.opened_by_decision_id
WHERE sd.operator_decision != sd.shadow_decision
  OR (sd.operator_decision IS NOT NULL AND sd.shadow_decision IS NULL)
  OR (sd.operator_decision IS NULL AND sd.shadow_decision IS NOT NULL)
ORDER BY sd.signal_created_at DESC
LIMIT 50;
```

### 4. Comparison Chart (Cumulative P&L)

```sql
-- Operator cumulative P&L
SELECT 
  DATE(closed_at) as date,
  SUM(pnl_usd) OVER (ORDER BY closed_at) as cumulative_pnl
FROM positions
WHERE mode = 'operator'
  AND status = 'closed'
  AND closed_at IS NOT NULL
  AND closed_at >= ? AND closed_at <= ?
ORDER BY closed_at;

-- Shadow cumulative P&L (аналогично с mode = 'shadow')
```

## Frontend Components

### Page: `/quality/operator-vs-shadow`

**Layout:**

```
┌─────────────────────────────────────────────────────────┐
│ Operator vs Shadow Comparison                           │
├─────────────────────────────────────────────────────────┤
│ Filters: [Date Range] [Market Category]                │
├─────────────────────────────────────────────────────────┤
│ Summary Cards:                                          │
│ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│ │Agreement │ │ Operator │ │  Shadow  │ │Performance│  │
│ │   Rate   │ │ Win Rate │ │ Win Rate │ │   Delta   │  │
│ │   75%    │ │   60%    │ │   55%    │ │   +5%     │  │
│ └──────────┘ └──────────┘ └──────────┘ └──────────┘  │
├─────────────────────────────────────────────────────────┤
│ Cumulative P&L Chart:                                   │
│ ┌─────────────────────────────────────────────────────┐ │
│ │  [Line chart: Operator vs Shadow over time]        │ │
│ └─────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│ Divergence Cases Table:                                 │
│ ┌─────────────────────────────────────────────────────┐ │
│ │ Market | Operator | Shadow | Outcome | P&L Delta  │ │
│ │ ───────┼──────────┼────────┼─────────┼────────────│ │
│ │ 123456 │ Accept   │ Reject │ Op +$50 │ +$50       │ │
│ │ 789012 │ Reject   │ Accept │ Sh +$30 │ -$30       │ │
│ └─────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────┘
```

### Components to Create

1. **`OperatorVsShadowPage.tsx`** - Main page component
2. **`ComparisonSummaryCards.tsx`** - KPI cards для summary
3. **`CumulativePnLChart.tsx`** - Dual-line chart для operator vs shadow
4. **`DivergenceCasesTable.tsx`** - Table с divergence cases
5. **`PerformanceDeltaBadge.tsx`** - Badge component для показа delta

## Implementation Plan

### Backend (Python)

1. **`python/comparison_metrics.py`** - новый модуль
   - `get_agreement_rate(conn, from_date, to_date)`
   - `get_mode_performance(conn, mode, from_date, to_date)`
   - `get_divergence_cases(conn, from_date, to_date, limit)`
   - `get_comparison_chart(conn, from_date, to_date)`
   - `get_operator_vs_shadow_comparison(conn, from_date, to_date)`

2. **`python/dashboard_models.py`** - добавить модели
   - `ModePerformance`
   - `PerformanceDelta`
   - `DivergenceCase`
   - `ComparisonChartPoint`
   - `OperatorVsShadowSummary`
   - `OperatorVsShadowResponse`

3. **`python/dashboard_api.py`** - добавить endpoint
   - `GET /api/quality/operator-vs-shadow`

### Frontend (TypeScript/React)

1. **`dashboard/src/types/api.ts`** - добавить типы
2. **`dashboard/src/api/client.ts`** - добавить метод
3. **`dashboard/src/pages/OperatorVsShadowPage.tsx`** - создать страницу
4. **`dashboard/src/components/ComparisonSummaryCards.tsx`** - создать компонент
5. **`dashboard/src/components/CumulativePnLChart.tsx`** - создать компонент
6. **`dashboard/src/components/DivergenceCasesTable.tsx`** - создать компонент
7. **`dashboard/src/app/routes.tsx`** - добавить route

## Acceptance Criteria

- [ ] API endpoint `/api/quality/operator-vs-shadow` возвращает корректные данные
- [ ] Dashboard показывает agreement rate между operator и shadow
- [ ] Cumulative P&L chart визуализирует performance обоих режимов
- [ ] Divergence cases table показывает случаи расхождения
- [ ] Performance delta cards показывают разницу в метриках
- [ ] Можно фильтровать по датам
- [ ] Можно drill down в конкретные divergence cases
- [ ] Highlight cases где operator outperformed shadow (learning opportunities)

## Метрики успеха

1. **Agreement Rate**: Показывает насколько operator и shadow синхронизированы
2. **Performance Delta**: Показывает кто лучше (operator или shadow)
3. **Divergence Analysis**: Помогает найти learning opportunities
4. **Cumulative P&L**: Визуализирует performance over time

## Риски и ограничения

1. **Недостаточно данных**: Если мало signals/decisions, метрики могут быть неточными
2. **Временные лаги**: Operator может принимать решения позже shadow
3. **Разные book balances**: Operator и shadow могут иметь разные балансы
4. **Incomplete data**: Не все signals имеют decisions от обоих режимов

## Следующие шаги

1. Создать backend модуль `comparison_metrics.py`
2. Добавить Pydantic модели
3. Добавить API endpoint
4. Создать frontend компоненты
5. Добавить route
6. Тестировать с реальными данными
7. Deploy на production
