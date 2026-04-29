# Command Center (Dual-Loop, Phase 1 / Track C)

Bloomberg-style operator console for the Pythia Dual-Loop architecture.
Route: `/command-center` — outside the main Layout so it takes over the viewport.

In Phase 1 the page is **read-only** and driven by an in-browser mock feed.
Phase 2 swaps the mock feed for a real WSS connection to `gateway-api` and
enables server-side mutations (accept/reject/snooze/kill). Phase 1 hotkeys
only touch UI state.

## Feature flag

- `VITE_DUAL_LOOP_UI_ENABLED=true` (in `dashboard/.env`) — route renders
  fully. When unset/`false`, the page shows an "Command Center disabled"
  placeholder unless the URL has `?preview=1`.
- `VITE_COMMAND_CENTER_WS_URL` — optional. When set, the client opens a
  real WebSocket and applies `snapshot` + `patch` (JSON Patch) frames. When
  unset, the mock feed runs instead.
- `VITE_COMMAND_CENTER_WS_TOKEN` — optional build-time token for dev/demo
  sessions. Safer alternative: open `/command-center?...&token=<paseto>`.
- `VITE_COMMAND_CENTER_WS_DEBUG=true` or page query `cc_debug=1` — logs WS
  connect / snapshot / patch application in the browser console.
- Page query `cc_ws_url=ws://.../ws/v1` — runtime override for the gateway
  WebSocket URL, useful on shared servers where we do not want to rebuild the
  dashboard for every demo/proof run.

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
├── components/KillModal.tsx         W7 — Ctrl+Shift+K placeholder "disabled in Phase 1"
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

| Keys              | Action                                                            |
| ----------------- | ----------------------------------------------------------------- |
| `j` / `k`         | Move Triage cursor                                                |
| `A`               | Mark current signal as *accept* locally + toast "Phase 1 local"   |
| `R`               | Mark as *reject* locally + toast                                  |
| `S`               | Mark as *snooze* locally + toast                                  |
| `Space`           | Toggle Market Detail modal on selected row                        |
| `Shift+A`         | Accept-all (Phase 1: toast only)                                  |
| `Shift+D`         | Dismiss-all (Phase 1: toast only)                                 |
| `Ctrl+Shift+K`    | Open Kill Switch disabled notice                                  |
| `Ctrl+Shift+P`    | Toggle dev FPS HUD                                                |
| `?` / `Ctrl+/`    | Toggle hotkey help overlay                                        |
| `Escape`          | Close any open modal                                              |

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

`__tests__/jsonPatch.test.ts`     — store + JSON Patch applier unit tests.
`__tests__/CommandCenter.smoke.test.tsx` — RTL smoke: renders all regions,
hotkeys (`j`/`k`, `A`, `R`, `Space`, `Ctrl+Shift+K`, `?`) update store state.

Run: `bun test` from `dashboard/`.

## Out of scope (Phase 1)

- Real WSS to gateway (arrives in Phase 2; see `ws/client.ts` skeleton).
- Functional Kill Switch — disabled placeholder only (Phase 3 ships the real
  two-key confirm).
- Responsive layout (desktop 1920×1080 only).
- Mutation endpoints on `gateway-api` (Phase 2 adds `/api/v1/signals/:id/accept`
  etc.).
