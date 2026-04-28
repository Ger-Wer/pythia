# Phase 5.1: Shadow Performance Dashboard — Design Document

**Дата:** 2026-04-13  
**Статус:** Design → Implementation  
**Приоритет:** HIGH  
**Оценка:** 3-4 дня  
**Цель:** Построить dashboard для валидации shadow track record и сравнения с operator decisions

---

## Требования

### Функциональные требования
1. **Новая страница** `/quality/shadow-performance`
2. **Метрики:**
   - Win rate (% profitable positions)
   - Average P&L per position
   - Total P&L (cumulative)
   - Best/worst performers
   - Position count (total, open, closed)
3. **Фильтры:**
   - По датам (date range)
   - По mode (shadow/operator/all)
   - По market category (если доступно)
4. **Визуализация:**
   - Equity curve (cumulative P&L over time)
   - Win/loss distribution (histogram)
   - P&L by position (bar chart)

### Нефункциональные требования
- Данные обновляются при каждом закрытии position
- Загрузка страницы < 2 секунды
- Responsive design (работает на мобильных)

---

## Архитектура

### Backend API

**Новый endpoint:** `GET /api/quality/shadow-performance`

**Query parameters:**
- `mode` (optional): `shadow` | `operator` | `all` (default: `shadow`)
- `from_date` (optional): ISO date string
- `to_date` (optional): ISO date string

**Response:**
```typescript
interface ShadowPerformanceResponse {
  summary: {
    total_positions: number;
    open_positions: number;
    closed_positions: number;
    win_rate: number;  // percentage
    avg_pnl_usd: number;
    total_pnl_usd: number;
    best_position: {
      market_id: string;
      pnl_usd: number;
      pnl_percent: number;
    } | null;
    worst_position: {
      market_id: string;
      pnl_usd: number;
      pnl_percent: number;
    } | null;
  };
  equity_curve: Array<{
    date: string;  // ISO date
    cumulative_pnl: number;
  }>;
  positions: Array<{
    id: number;
    market_id: string;
    mode: string;
    status: string;
    pnl_usd: number | null;
    pnl_percent: number | null;
    opened_at: string;
    closed_at: string | null;
  }>;
}
```

### SQL Queries

#### 1. Summary metrics
```sql
SELECT 
  COUNT(*) as total_positions,
  COUNT(CASE WHEN status = 'open' THEN 1 END) as open_positions,
  COUNT(CASE WHEN status = 'closed' THEN 1 END) as closed_positions,
  -- Win rate (только для closed positions)
  CAST(COUNT(CASE WHEN status = 'closed' AND pnl_usd > 0 THEN 1 END) AS REAL) * 100.0 / 
    NULLIF(COUNT(CASE WHEN status = 'closed' THEN 1 END), 0) as win_rate,
  -- Average P&L (только для closed positions)
  AVG(CASE WHEN status = 'closed' THEN pnl_usd END) as avg_pnl_usd,
  -- Total P&L (только для closed positions)
  SUM(CASE WHEN status = 'closed' THEN pnl_usd END) as total_pnl_usd
FROM positions
WHERE mode = ? -- 'shadow' or 'operator'
  AND (? IS NULL OR opened_at >= ?)  -- from_date filter
  AND (? IS NULL OR opened_at <= ?); -- to_date filter
```

#### 2. Best/worst positions
```sql
-- Best position
SELECT market_id, pnl_usd, pnl_percent
FROM positions
WHERE mode = ? 
  AND status = 'closed'
  AND pnl_usd IS NOT NULL
  AND (? IS NULL OR opened_at >= ?)
  AND (? IS NULL OR opened_at <= ?)
ORDER BY pnl_usd DESC
LIMIT 1;

-- Worst position
SELECT market_id, pnl_usd, pnl_percent
FROM positions
WHERE mode = ? 
  AND status = 'closed'
  AND pnl_usd IS NOT NULL
  AND (? IS NULL OR opened_at >= ?)
  AND (? IS NULL OR opened_at <= ?)
ORDER BY pnl_usd ASC
LIMIT 1;
```

#### 3. Equity curve
```sql
SELECT 
  DATE(closed_at) as date,
  SUM(pnl_usd) OVER (ORDER BY closed_at) as cumulative_pnl
FROM positions
WHERE mode = ?
  AND status = 'closed'
  AND closed_at IS NOT NULL
  AND pnl_usd IS NOT NULL
  AND (? IS NULL OR closed_at >= ?)
  AND (? IS NULL OR closed_at <= ?)
ORDER BY closed_at;
```

#### 4. All positions (for table)
```sql
SELECT 
  id,
  market_id,
  mode,
  status,
  pnl_usd,
  pnl_percent,
  opened_at,
  closed_at
FROM positions
WHERE mode = ?
  AND (? IS NULL OR opened_at >= ?)
  AND (? IS NULL OR opened_at <= ?)
ORDER BY opened_at DESC
LIMIT 100;
```

---

## Frontend Components

### 1. ShadowPerformancePage.tsx
**Путь:** `dashboard/src/pages/ShadowPerformancePage.tsx`

**Структура:**
```tsx
export function ShadowPerformancePage() {
  const [mode, setMode] = useState<'shadow' | 'operator' | 'all'>('shadow');
  const [dateRange, setDateRange] = useState<{from: string | null, to: string | null}>({from: null, to: null});
  const [data, setData] = useState<ShadowPerformanceResponse | null>(null);
  
  // Fetch data on mount and when filters change
  useEffect(() => {
    fetchShadowPerformance(mode, dateRange).then(setData);
  }, [mode, dateRange]);
  
  return (
    <div className="page-container">
      <header>
        <h1>Shadow Performance</h1>
        <FilterBar mode={mode} onModeChange={setMode} dateRange={dateRange} onDateRangeChange={setDateRange} />
      </header>
      
      <PerformanceMetrics summary={data?.summary} />
      <EquityCurveChart data={data?.equity_curve} />
      <PositionsTable positions={data?.positions} />
    </div>
  );
}
```

### 2. PerformanceMetrics.tsx
**Путь:** `dashboard/src/components/PerformanceMetrics.tsx`

**Компонент:** Grid из KPI cards
- Total Positions
- Win Rate (с цветовой индикацией: >50% = green, <50% = red)
- Avg P&L
- Total P&L
- Best Position
- Worst Position

### 3. EquityCurveChart.tsx
**Путь:** `dashboard/src/components/EquityCurveChart.tsx`

**Визуализация:** Line chart с cumulative P&L по оси Y и датой по оси X
- Использовать SVG (как в ProbabilityHistoryChart)
- Цвет линии: green если cumulative_pnl > 0, red если < 0
- Показывать grid lines для читаемости
- Tooltip при hover с точной датой и P&L

### 4. PositionsTable.tsx
**Путь:** `dashboard/src/components/PositionsTable.tsx`

**Таблица:**
| Market ID | Mode | Status | P&L USD | P&L % | Opened | Closed |
|-----------|------|--------|---------|-------|--------|--------|

- Сортировка по колонкам
- Цветовая индикация P&L (green/red)
- Клик на market_id → переход к `/markets/{market_id}`

### 5. FilterBar.tsx
**Путь:** `dashboard/src/components/FilterBar.tsx`

**Фильтры:**
- Mode selector: Radio buttons (Shadow / Operator / All)
- Date range picker: From / To inputs (type="date")
- Reset button

---

## Implementation Plan

### Day 1: Backend API
- [ ] Создать `python/quality_metrics.py` с функциями для SQL queries
- [ ] Добавить endpoint в `python/dashboard_api.py`: `GET /api/quality/shadow-performance`
- [ ] Добавить типы в `dashboard/src/types/api.ts`: `ShadowPerformanceResponse`
- [ ] Добавить API client method в `dashboard/src/api/client.ts`
- [ ] Тестировать API с curl/Postman

### Day 2: Frontend Components (Part 1)
- [ ] Создать `PerformanceMetrics.tsx` с KPI cards
- [ ] Создать `EquityCurveChart.tsx` с line chart
- [ ] Добавить стили в `styles.css`
- [ ] Тестировать компоненты с mock data

### Day 3: Frontend Components (Part 2)
- [ ] Создать `PositionsTable.tsx` с сортировкой
- [ ] Создать `FilterBar.tsx` с фильтрами
- [ ] Создать `ShadowPerformancePage.tsx` с интеграцией всех компонентов
- [ ] Добавить route в `dashboard/src/app/routes.tsx`
- [ ] Добавить ссылку в navigation

### Day 4: Testing & Deployment
- [ ] End-to-end тестирование с реальными данными
- [ ] Проверить responsive design
- [ ] Проверить performance (загрузка < 2 сек)
- [ ] Закоммитить изменения
- [ ] Задеплоить на сервер
- [ ] Проверить на production

---

## Acceptance Criteria

✅ Dashboard показывает win rate и avg P&L для shadow positions  
✅ Equity curve визуализирует cumulative performance  
✅ Можно фильтровать по mode (shadow/operator/all)  
✅ Можно фильтровать по датам (from/to)  
✅ Таблица показывает все positions с сортировкой  
✅ Best/worst positions выделены  
✅ Данные обновляются при каждом закрытии position  
✅ Страница загружается < 2 секунды  
✅ Responsive design работает на мобильных  

---

## Следующие шаги (Phase 5.2)

После завершения Phase 5.1:
- Phase 5.2: Operator vs Shadow Comparison (side-by-side comparison)
- Phase 5.3: Review Discipline Metrics (review completion rate, overdue reviews)
- Phase 5.4: Weekly Decision Review (automated weekly reports)
