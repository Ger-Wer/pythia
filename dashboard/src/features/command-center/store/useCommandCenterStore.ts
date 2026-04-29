import { create } from "zustand";
import { applyPatch, type Operation } from "fast-json-patch";
import type {
  CommandCenterSnapshot,
  CommandCenterState,
  LocalDecision,
  Signal,
  TriggerEvent,
  WatchlistRow,
} from "./types";
import { makeInitialState } from "./initialState";

interface CommandCenterActions {
  /** Replace entire state from a snapshot (from WS on connect). */
  applySnapshot: (next: CommandCenterSnapshot) => void;
  /** Apply an RFC6902 JSON Patch delta. */
  applyDelta: (ops: Operation[]) => void;
  /** Local helpers for mock feed. */
  addSignal: (s: Signal) => void;
  removeSignal: (id: string) => void;
  upsertWatchlistRow: (row: WatchlistRow) => void;
  pushTrigger: (t: TriggerEvent) => void;
  pushLatencySample: (
    bucket: "mempoolToBroadcast" | "broadcastToMined" | "gammaPoll",
    value: number,
  ) => void;
  setHealth: (patch: Partial<CommandCenterState["health"]>) => void;
  moveCursor: (delta: number) => void;
  setCursor: (index: number) => void;
  setDetailOpen: (open: boolean) => void;
  setKillModalOpen: (open: boolean) => void;
  setHelpOpen: (open: boolean) => void;
  togglePerfHud: () => void;
  setLocalDecision: (id: string, decision: LocalDecision) => void;
  setNow: (now: number) => void;
  reset: () => void;
}

export type CommandCenterStore = CommandCenterState & CommandCenterActions;

const MAX_TRIGGERS = 50;
const LATENCY_WINDOW = 60;

export const useCommandCenterStore = create<CommandCenterStore>()((set) => ({
  ...makeInitialState(),

  applySnapshot: (next) =>
    set((s) => ({
      ...s,
      ...next,
      health: next.health ? { ...s.health, ...next.health } : s.health,
      latency: next.latency ? { ...s.latency, ...next.latency } : s.latency,
      cursor: next.cursor ? { ...s.cursor, ...next.cursor } : s.cursor,
    })),

  applyDelta: (ops) =>
    set((s) => {
      // We only patch the serializable slice (omit transient bits).
      const slice: CommandCenterState = {
        signals: s.signals,
        signalOrder: s.signalOrder,
        watchlist: s.watchlist,
        watchlistOrder: s.watchlistOrder,
        triggers: s.triggers,
        health: s.health,
        latency: s.latency,
        cursor: s.cursor,
        detailOpen: s.detailOpen,
        killModalOpen: s.killModalOpen,
        helpOpen: s.helpOpen,
        perfHudOpen: s.perfHudOpen,
        now: s.now,
      };
      const next = applyPatch(
        structuredClone(slice),
        ops,
        /*validateOperation*/ false,
        /*mutateDocument*/ true,
      ).newDocument;
      return { ...s, ...next };
    }),

  addSignal: (sig) =>
    set((s) => {
      if (s.signals[sig.id]) return s;
      return {
        signals: { ...s.signals, [sig.id]: sig },
        signalOrder: [sig.id, ...s.signalOrder],
      };
    }),

  removeSignal: (id) =>
    set((s) => {
      if (!s.signals[id]) return s;
      const { [id]: _removed, ...rest } = s.signals;
      void _removed;
      const nextOrder = s.signalOrder.filter((x) => x !== id);
      let cursor = s.cursor.triageIndex;
      if (cursor >= nextOrder.length) cursor = Math.max(0, nextOrder.length - 1);
      return { signals: rest, signalOrder: nextOrder, cursor: { triageIndex: cursor } };
    }),

  upsertWatchlistRow: (row) =>
    set((s) => {
      const exists = Boolean(s.watchlist[row.market]);
      return {
        watchlist: { ...s.watchlist, [row.market]: row },
        watchlistOrder: exists ? s.watchlistOrder : [...s.watchlistOrder, row.market],
      };
    }),

  pushTrigger: (t) =>
    set((s) => ({ triggers: [t, ...s.triggers].slice(0, MAX_TRIGGERS) })),

  pushLatencySample: (bucket, value) =>
    set((s) => {
      const next = [...s.latency[bucket], value];
      if (next.length > LATENCY_WINDOW) next.shift();
      return { latency: { ...s.latency, [bucket]: next } };
    }),

  setHealth: (patch) => set((s) => ({ health: { ...s.health, ...patch } })),

  moveCursor: (delta) =>
    set((s) => {
      const len = s.signalOrder.length;
      if (len === 0) return { cursor: { triageIndex: 0 } };
      const next = Math.max(0, Math.min(len - 1, s.cursor.triageIndex + delta));
      return { cursor: { triageIndex: next } };
    }),

  setCursor: (index) =>
    set((s) => {
      const len = s.signalOrder.length;
      const clamped = len === 0 ? 0 : Math.max(0, Math.min(len - 1, index));
      return { cursor: { triageIndex: clamped } };
    }),

  setDetailOpen: (open) => set({ detailOpen: open }),
  setKillModalOpen: (open) => set({ killModalOpen: open }),
  setHelpOpen: (open) => set({ helpOpen: open }),
  togglePerfHud: () => set((s) => ({ perfHudOpen: !s.perfHudOpen })),

  setLocalDecision: (id, decision) =>
    set((s) => {
      const sig = s.signals[id];
      if (!sig) return s;
      return { signals: { ...s.signals, [id]: { ...sig, localDecision: decision } } };
    }),

  setNow: (now) => set({ now }),

  reset: () => set(makeInitialState()),
}));
