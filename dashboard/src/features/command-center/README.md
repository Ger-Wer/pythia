# Command Center (Dual-Loop, Phase 1 / Track C)

Bloomberg-style operator console for the Pythia Dual-Loop architecture.
Route: `/command-center` — outside the main Layout so it takes over the viewport.

The page is driven by either an in-browser mock feed (when no
`VITE_COMMAND_CENTER_WS_URL` is set) or a real WSS connection to
`gateway-api`. Operator decision hotkeys (`A`/`R`/`S`) write through to the
gateway when `VITE_COMMAND_CENTER_API_URL` + token + `operator_id` are
configured; otherwise they only update the local store (preview / mock
mode). The Kill Switch (`Ctrl+Shift+K`) signs and posts a real
`POST /api/v1/control/kill` when
`VITE_COMMAND_CENTER_KILL_HMAC_SECRET` is also configured, otherwise the
modal renders read-only. Bulk actions (`Shift+A`, `Shift+D`) are still
stubs and ship in a later phase.

## Feature flag

- `VITE_DUAL_LOOP_UI_ENABLED=true` (in `dashboard/.env`) — route renders
  fully. When unset/`false`, the page shows an "Command Center disabled"
  placeholder unless the URL has `?preview=1`.
- `VITE_COMMAND_CENTER_WS_URL` — optional. When set, the client opens a
  real WebSocket and applies `snapshot` + `patch` (JSON Patch) frames. When
  unset, the mock feed runs instead.
- `VITE_COMMAND_CENTER_WS_TOKEN` — optional build-time token for dev/demo
  sessions. Safer alternative: open `/command-center?...&token=<paseto>`.
  The same token is reused for HTTP `Authorization: Bearer <token>` on
  decision write-path calls.
- `VITE_COMMAND_CENTER_WS_DEBUG=true` or page query `cc_debug=1` — logs WS
  connect / snapshot / patch application in the browser console.
- Page query `cc_ws_url=ws://.../ws/v1` — runtime override for the gateway
  WebSocket URL, useful on shared servers where we do not want to rebuild the
  dashboard for every demo/proof run.
- `VITE_COMMAND_CENTER_API_URL` (or page query `cc_api_url=` /
  `api_url=`) — HTTP base URL for `POST /api/v1/signals/:id/{accept,reject,snooze}`.
  When unset, derived from `VITE_COMMAND_CENTER_WS_URL` by replacing the
  protocol (`ws`/`wss` → `http`/`https`) and dropping the path. Required for
  operator decisions to actually reach the gateway.
- `VITE_COMMAND_CENTER_OPERATOR_ID` (or page query `operator_id=`) — value
  sent in `body.operator_id`. Must match the `sub` claim of the PASETO
  token. Required for operator decisions.
- `VITE_COMMAND_CENTER_KILL_HMAC_SECRET` (or page query `kill_hmac=`) —
  shared HMAC-SHA256 secret for the Kill Switch. Must match
  `KILL_HMAC_SECRET` on `gateway-api`. The dashboard signs every kill
  command with this secret in the browser. When unset, `Ctrl+Shift+K`
  still opens the modal but renders read-only with an explanatory
  banner.

See `dashboard/.env.example`.

## Component map

```
CommandCenterPage.tsx         route entry, mounts feed, wires hotkeys
├── components/StatusBar.tsx         W1 — banner (Redis/NATS/Polygon ping, LLM budget, op TTD, kill status)
├── components/TriageQueue.tsx       W2 — virtualized list, j/k cursor, local decision stripe
├── components/Watchlist.tsx         W3 — virtualized table (market, side, edge, liq, depth, slip, trigger)
├── components/EdgeHeatmap.tsx       W4 — canvas scatter, X=log|edge|, Y=liquidity, color=confidence, size=pos
├── components/ExecutionPanel.tsx    W5 — last N triggers (fired/blocked/submitted/filled/reverted) + copy txhash
├── components/LatencyHistograms.tsx W6 — 3 SVG sparklines (mempool→broadcast, broadcast→mined, Gamma poll)
├── components/MarketDetailModal.tsx W8 — Space on selected triage row
├── components/KillModal.tsx         W7 — Ctrl+Shift+K live two-key kill (scope/reason/confirm phrase)
├── components/HotkeyHelp.tsx        ?/Ctrl+/ overlay
├── components/PerfHud.tsx           Ctrl+Shift+P dev FPS counter
└── components/ToastHost.tsx         bottom-right transient messages
```

## State shape

`store/useCommandCenterStore.ts` — one Zustand store, consumed via
`useShallow` selectors per widget so unrelated updates don't re-render.

```ts
{
  signals:     Record<id, Signal>
  signalOrder: string[]
  watchlist:   Record<market, WatchlistRow>
  watchlistOrder: string[]
  triggers:    TriggerEvent[]     // ring-buffer, last 50
  health:      SystemHealth       // Redis/NATS/Polygon ping, LLM budget, WS, kill
  latency:     { mempoolToBroadcast[], broadcastToMined[], gammaPoll[] }
  cursor:      { triageIndex }
  detailOpen, killModalOpen, helpOpen, perfHudOpen: boolean
  now:         number              // epoch ms, ticked by feed
}
```

`applySnapshot({...})` — replace slice from WS `snapshot` frame.
`applyDelta(ops)` — apply RFC 6902 JSON Patch from WS `delta` frame via
`fast-json-patch`.

## Mock feed (Phase 1)

`mock/mock_feed.ts` seeds ~6 signals + 10 watchlist rows, then runs:

| Interval | What it does                                          |
| -------- | ----------------------------------------------------- |
| 1 s      | `now` clock tick, health + latency samples            |
| 5 s      | Push a new signal (and drop oldest if > 40)           |
| 10 s     | Mutate a random watchlist row's edge/depth/trigger    |
| 15 s     | Push a trigger event (fired/blocked/submitted/filled) |

## Hotkeys

| Keys              | Action                                                                              |
| ----------------- | ----------------------------------------------------------------------------------- |
| `j` / `k`         | Move Triage cursor                                                                  |
| `A`               | Optimistically *accept* + `POST /api/v1/signals/:id/accept` (rollback on 409/404/403) |
| `R`               | Optimistically *reject* + `POST /api/v1/signals/:id/reject` (default reason)        |
| `S`               | Optimistically *snooze* + `POST /api/v1/signals/:id/snooze` (default 1h)            |
| `Space`           | Toggle Market Detail modal on selected row                                          |
| `Shift+A`         | Accept-all — not wired yet                                                          |
| `Shift+D`         | Dismiss-all — not wired yet                                                         |
| `Ctrl+Shift+K`    | Open Kill Switch (two-key: phrase + `Ctrl+Enter` / `Ctrl+click FIRE`)               |
| `Ctrl+Shift+P`    | Toggle dev FPS HUD                                                                  |
| `?` / `Ctrl+/`    | Toggle hotkey help overlay                                                          |
| `Escape`          | Close any open modal                                                                |

Hotkeys auto-disable when focus is on an input/textarea/select, or when
the focused element has `data-keyboard-safe="true"`.

## Performance

- `@tanstack/react-virtual` for Triage Queue and Watchlist (handles 200+ rows).
- `React.memo` + fine-grained `useShallow` selectors per widget.
- Latency rings are fixed-size (60 samples) to cap memory.
- `EdgeHeatmap` paints on a single `<canvas>` with DPR scaling.

## Accessibility

- Viridis palette for the heatmap, plus colour-blind safe ARIA labels on all
  panel regions.
- `:focus-visible` ring (`--cc-focus-ring: 0 0 0 2px #40c4ff`) on every
  interactive element.
- `@axe-core/react` runs in dev mode from `src/main.tsx` and logs violations
  to the console (Phase 1 target: 0 critical).

## Tests

`__tests__/jsonPatch.test.ts`            — store + JSON Patch applier unit tests.
`__tests__/CommandCenter.smoke.test.tsx` — RTL smoke: renders all regions,
hotkeys (`j`/`k`, `A`, `R`, `Space`, `Ctrl+Shift+K`, `?`) update store state.
`__tests__/KillModal.smoke.test.tsx`     — RTL: live submit flow (auth gate,
two-key submit, MARKET scope, error states).
`api/__tests__/decideClient.test.ts`     — write-path HTTP client unit tests.
`api/__tests__/killClient.test.ts`       — kill HTTP client + HMAC unit tests.

Run: `bun test` from `dashboard/`.

## Kill Switch flow

1. `Ctrl+Shift+K` opens the modal (always — even without auth, in
   read-only mode).
2. Operator picks a scope: `GLOBAL`, `NEW_TRIGGERS`, or `MARKET`. For
   `MARKET`, a `market_id` field appears.
3. Operator types a `reason` (8–256 chars) — recorded in the gateway
   audit log under `gateway:audit:kill`.
4. Operator types the confirm phrase. The phrase is `KILL <SCOPE>` for
   GLOBAL/NEW_TRIGGERS or `KILL <market_id>` for MARKET.
5. Operator submits with the second key — either `Ctrl+Enter` while
   focused on the confirm input, or `Ctrl+click` (or `Cmd+click` on
   macOS) on the FIRE button. Plain Enter / plain click do not fire.
6. Dashboard computes `HMAC_SHA256(killHmacSecret,
   operator_id|scope|market_id|nonce|issued_at_unix_ms)` via
   `crypto.subtle`, attaches `Bearer <token>`, and POSTs the kill
   command. The reason is sent in the body but is NOT signed.
7. The modal renders the gateway response: `COMPLETE` (green) means
   the executor confirmed the drain; `ACCEPTED`/`DRAINING` (amber)
   means the executor accepted but is still draining; `timeout` /
   `no_bus` (red) means the dual-loop side did not ack within
   `KILL_ACK_TIMEOUT_MS` and operator must verify state manually;
   `REJECTED_BAD_SIGNATURE` / `REJECTED_REPLAY` (red) point at clock
   skew / nonce reuse / wrong shared secret.

## Out of scope (Phase 1)

- Real WSS to gateway (arrives in Phase 2; see `ws/client.ts` skeleton).
- Responsive layout (desktop 1920×1080 only).
- Mutation endpoints on `gateway-api` (Phase 2 adds `/api/v1/signals/:id/accept`
  etc.).
