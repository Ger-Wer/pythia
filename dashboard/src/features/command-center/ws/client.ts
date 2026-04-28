import type { Operation } from "fast-json-patch";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import { startMockFeed } from "../mock/mock_feed";

type IncomingFrame =
  | { kind: "snapshot"; data: Record<string, unknown> }
  | { kind: "delta"; ops: Operation[] }
  | { kind: "pong" };

export interface CommandCenterFeedHandle {
  stop: () => void;
  mode: "mock" | "ws";
}

/**
 * Phase 1: if VITE_COMMAND_CENTER_WS_URL is set, attempt real WSS connection.
 * Otherwise fall back to the mock feed. Phase 2 will wire this to the gateway.
 */
export function startCommandCenterFeed(): CommandCenterFeedHandle {
  const wsUrl = (import.meta.env.VITE_COMMAND_CENTER_WS_URL as string | undefined)?.trim();
  if (!wsUrl) {
    const handle = startMockFeed();
    return { stop: handle.stop, mode: "mock" };
  }

  let closed = false;
  let ws: WebSocket | null = null;
  let backoff = 100;
  let reconnectTimer: number | null = null;
  let heartbeatTimer: number | null = null;

  const connect = () => {
    if (closed) return;
    try {
      ws = new WebSocket(wsUrl);
    } catch {
      scheduleReconnect();
      return;
    }
    ws.addEventListener("open", () => {
      backoff = 100;
      useCommandCenterStore.getState().setHealth({ wsConnected: true });
      heartbeatTimer = window.setInterval(() => {
        try {
          ws?.send(JSON.stringify({ op: "ping" }));
        } catch {
          /* noop */
        }
      }, 1000);
    });
    ws.addEventListener("message", (evt) => {
      try {
        const frame = JSON.parse(String(evt.data)) as IncomingFrame;
        const store = useCommandCenterStore.getState();
        if (frame.kind === "snapshot") {
          store.applySnapshot(frame.data as never);
        } else if (frame.kind === "delta") {
          store.applyDelta(frame.ops);
        }
      } catch {
        /* ignore malformed frames */
      }
    });
    ws.addEventListener("close", () => {
      useCommandCenterStore.getState().setHealth({ wsConnected: false });
      if (heartbeatTimer != null) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
      }
      scheduleReconnect();
    });
    ws.addEventListener("error", () => {
      try {
        ws?.close();
      } catch {
        /* noop */
      }
    });
  };

  const scheduleReconnect = () => {
    if (closed) return;
    if (reconnectTimer != null) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      backoff = Math.min(5000, backoff * 2);
      connect();
    }, backoff);
  };

  connect();

  return {
    stop: () => {
      closed = true;
      if (reconnectTimer != null) clearTimeout(reconnectTimer);
      if (heartbeatTimer != null) clearInterval(heartbeatTimer);
      try {
        ws?.close();
      } catch {
        /* noop */
      }
    },
    mode: "ws",
  };
}
