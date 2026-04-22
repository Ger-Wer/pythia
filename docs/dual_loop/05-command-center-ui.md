# 05 — Command Center UI/UX

## Принципы

1. **1-Second Rule** — каждый критический факт считывается за один саккадический скан.
2. **Zero-text-where-possible** — числа и цвет вместо слов; текст только для market question.
3. **Information density > aesthetics** — монохромный фон (`#0B0D10`), данные контрастом.
4. **Fitts' Law** — ключевые действия (Accept, Kill) в углах экрана и на hotkeys.
5. **Fail-loud** — любой degradation (stale WS, RPC timeout, high latency) должен быть визуально невозможно игнорировать (pulsing red border).

## Layout (сетка 12×12, fixed, 1920×1080 baseline, responsive до 4K)

```
┌──────────────────────────────────────────────────────────────────────┐
│  [STATUS BAR]  Node: 12ms | Mempool: 842 tx/s | WS: OK | PnL: +$1.2k │  (row 1)
├───────────────────┬──────────────────────────────┬───────────────────┤
│                   │                              │                   │
│  TRIAGE QUEUE     │  ACTIVE SNIPER WATCHLIST     │  EDGE HEATMAP     │
│  (col 1-3)        │  (col 4-9, HERO)             │  (col 10-12)      │
│                   │                              │                   │
│  [card]           │  ┌─ market A ───────────┐    │  ▓▓▓▓▓░░░░░       │
│  Edge: +17%       │  │ OB depth viz         │    │  ▓▓▓░░░░░░░       │
│  [A][R][S]        │  │ slippage: 0.3%       │    │  ▓▓▓▓▓▓▓░░░       │
│                   │  │ trigger: ARMED ●     │    │                   │
│  [card]           │  │ latency-to-fill est. │    │  (markets by      │
│                   │  └──────────────────────┘    │   |Edge| × liq)   │
│                   │  ┌─ market B ───────────┐    │                   │
│                   │  │ ...                  │    │                   │
│                   │  └──────────────────────┘    │                   │
├───────────────────┴──────────────────────────────┼───────────────────┤
│  EXECUTION PANEL (col 1-9, row 10-12)            │  PORTFOLIO (10-12)│
│  [MANUAL OVERRIDE]  [ARM]  [DISARM]              │  Positions: 4     │
│  [ KILL SWITCH ]   (requires 2-key)              │  Open risk: $8.4k │
│  Last action: Fill 0xabc...  +$230  12ms ago     │  Brier: 0.18      │
└──────────────────────────────────────────────────┴───────────────────┘
```

## Виджеты

| # | Виджет | Назначение | Данные (источник → канал) | Update rate |
|---|--------|------------|---------------------------|-------------|
| W1 | **Status Bar** | Health one-liner | `telem.ping`, `telem.mempool`, WS heartbeat | 250ms |
| W2 | **Triage Queue** | Очередь сигналов на approve | `signals.pending` → WS `ui.signals` | on push |
| W3 | **Signal Card** | Одна единица решения: Edge, P_fund, P_market, EV, confidence band, LLM rationale (collapsed) | snapshot из card DTO | static until updated |
| W4 | **Active Watchlist Row** | Рынок в работе Sniper'а: OB depth (sparkline), current market price, computed edge, slippage at size, trigger state (IDLE/ARMED/FIRED/COOLDOWN) | NATS `telem.ob.<market>`, `triggers.*` | 50–100ms |
| W5 | **Edge Heatmap** | Тепловая карта всех наблюдаемых рынков: ось X = liquidity, Y = abs(edge), color = staleness LLM-прогноза | Pythia digest | 1s |
| W6 | **Execution Panel** | Manual override controls, размер позиции slider, Arm/Disarm per-market | REST POST + WS confirm | sync |
| W7 | **Kill Switch** | Аварийная остановка всего executor'а | REST `/control/kill` двойной ключ | sync |
| W8 | **Portfolio Panel** | Открытые позиции, unrealized PnL, margin usage, Brier score paper vs live | Pythia aggregate | 1s |
| W9 | **Latency Strip** (в W1) | bar-chart p50/p95/p99 node RTT за последние 60s | RedisTS query | 500ms |
| W10 | **Alert Toasts** | Transient: trigger armed, position filled, stale data | bus `triggers.*`, `exec.*` | on push |

## Визуальная семантика

| Цвет | Hex | Значение |
|------|-----|----------|
| Green | `#00E676` | Edge > +threshold, healthy, fill success |
| Amber | `#FFB300` | Warning, stale data 5–15s, low confidence, trigger IDLE |
| Red (pulse) | `#FF1744` | Critical: trigger ARMED, position at risk, WS disconnected, kill engaged |
| Grey | `#546E7A` | Inactive, cooldown, rejected |

- **Heatmap** — viridis palette, reserved для magnitude.
- **Blink/pulse** — только для `trigger=ARMED` и `kill=ENGAGED` (max 2 активных pulsing элементов на экране одновременно).
- **Typography** — JetBrains Mono 12px для чисел, Inter 14px для лейблов. Табуляция всех чисел моноширинно.

## WebSocket-протокол

- Один persistent WSS `/ws/v1` от gateway, мультиплексированный по topic.
- Клиент подписывается: `{op: "sub", topics: ["ui.signals","telem.ob.*","triggers.*"]}`.
- Сервер отправляет:
  - **Snapshot** — полный state (на connect и каждые 30s для recovery).
  - **Delta** — JSON Patch (RFC 6902) операции: `[{op:"replace",path:"/signals/SIG123/edge",value:0.18}]`.
- Heartbeat: 1s ping; на 3 пропуска — клиент окрашивает весь UI в degraded mode.
- Reconnect: exponential backoff 100ms → 5s, при восстановлении — сервер присылает snapshot, клиент diff'ает локальный state.

### State management (client-side)

- **Zustand** store с selector-based subscriptions.
- Hot-row компоненты (Watchlist Row) изолированы через `memo` + `areEqual` по `version`.
- Delta apply — через `applyPatch` из `fast-json-patch`.
- Time-series буферы (OB depth, latency) — фиксированного размера ring buffer, чтобы не течь памяти.

## Hotkeys (ergonomics)

Раскладка оптимизирована для левой руки на клавиатуре, правая — на мыши/трекболе.

| Key | Действие | Скоуп |
|-----|----------|-------|
| `A` | Accept focused signal | Triage Queue |
| `R` | Reject focused signal | Triage Queue |
| `S` | Snooze 15m | Triage Queue |
| `J` / `K` | Navigate signals (vim-style) | Triage Queue |
| `Space` | Toggle detail (LLM rationale) | Focused card |
| `1..9` | Focus watchlist row N | Watchlist |
| `Shift+A` | Arm trigger on focused market | Watchlist |
| `Shift+D` | Disarm trigger on focused market | Watchlist |
| `M` | Manual override modal | Execution |
| `Ctrl+Shift+K` → confirm `Ctrl+Shift+K` | **Kill Switch** (two-step) | Global |
| `G G` | Go to Portfolio | Global |
| `?` | Hotkey cheatsheet | Global |

## Эргономика действий

- Каждое destructive action (Kill, Manual override fill) требует 2-keystroke confirm с visible countdown (3s abort window).
- Hotkeys выключаются автоматически при focus на input-полях (флаг `data-keyboard-safe`).
- После Accept сигнала — фокус автоматически переходит на следующую карту (zero mouse).
- Kill Switch физически разнесён с остальными кнопками (нижний правый угол), требует двойное нажатие с 3s countdown.

## Accessibility

- Обязательный контраст WCAG 2.1 AA для всех стейтов (проверяется в CI через `axe-core`).
- Цветокодирование дублируется иконкой (для дальтоников).
- Focus ring видимый, толщина 2px, цвет `#40C4FF`.
- Все actions доступны с клавиатуры.

## Переиспользование существующего dashboard

Существующий `dashboard/` (Vite + React) — отправная точка. В Phase 1:
- Добавить route `/command-center` с новым layout'ом.
- Оставить старые страницы (`/summary`, `/signals`, `/market-review`, `/favorites`) как режим "slow mode".
- Оператор может переключаться между режимами через header-toggle.

В Phase 2 `/command-center` становится default landing page.
