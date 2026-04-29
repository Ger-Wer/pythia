import { useCommandCenterStore } from "../store/useCommandCenterStore";
import { startMockFeed } from "../mock/mock_feed";
import {
  ALL_GATEWAY_TOPICS,
  createGatewayProjection,
  type GatewayPatchFrame,
  type GatewaySnapshotFrame,
} from "./gatewayAdapter";

type IncomingFrame =
  | GatewaySnapshotFrame
  | GatewayPatchFrame
  | { kind: "ping"; ts: number }
  | { kind: "error"; code: string; message: string };

export interface CommandCenterFeedHandle {
  stop: () => void;
  mode: "mock" | "ws";
}

interface FeedLogger {
  info: (message: string, meta?: unknown) => void;
  warn: (message: string, meta?: unknown) => void;
}

interface StartCommandCenterFeedOptions {
  wsUrl?: string;
  wsToken?: string;
  debug?: boolean;
  locationSearch?: string;
  logger?: FeedLogger;
  startMockFeed?: typeof startMockFeed;
}

function truthy(value: string | undefined): boolean {
  return value === "1" || value === "true";
}

function appendToken(baseUrl: string, token: string): string {
  const fallbackBase =
    typeof window !== "undefined" ? window.location.href : "http://localhost";
  const parsed = new URL(baseUrl, fallbackBase);
  if (parsed.protocol === "http:") parsed.protocol = "ws:";
  if (parsed.protocol === "https:") parsed.protocol = "wss:";
  if (!parsed.searchParams.get("token")) {
    parsed.searchParams.set("token", token);
  }
  return parsed.toString();
}

function sanitizeUrl(value: string): string {
  const parsed = new URL(
    value,
    typeof window !== "undefined" ? window.location.href : "http://localhost",
  );
  parsed.searchParams.delete("token");
  return parsed.toString();
}

function resolveWsConfig(options: StartCommandCenterFeedOptions): {
  wsUrl?: string;
  debug: boolean;
} {
  const search =
    options.locationSearch ??
    (typeof window !== "undefined" ? window.location.search : "");
  const params = new URLSearchParams(search);
  const envUrl = (import.meta.env.VITE_COMMAND_CENTER_WS_URL as string | undefined)?.trim();
  const envToken = (import.meta.env.VITE_COMMAND_CENTER_WS_TOKEN as string | undefined)?.trim();
  const envDebug = (import.meta.env.VITE_COMMAND_CENTER_WS_DEBUG as string | undefined)?.trim();

  const queryWsUrl = params.get("cc_ws_url")?.trim() ?? params.get("ws_url")?.trim();
  const baseUrl = options.wsUrl ?? queryWsUrl ?? envUrl;
  const token = options.wsToken ?? params.get("token")?.trim() ?? envToken;
  const debugFromQuery = params.get("cc_debug") === "1";
  const debug = options.debug ?? (debugFromQuery || truthy(envDebug));

  if (!baseUrl) {
    return { wsUrl: undefined, debug: Boolean(debug) };
  }

  return {
    wsUrl: token ? appendToken(baseUrl, token) : baseUrl,
    debug: Boolean(debug),
  };
}

export function startCommandCenterFeed(
  options: StartCommandCenterFeedOptions = {},
): CommandCenterFeedHandle {
  const logger = options.logger ?? {
    info: (message: string, meta?: unknown) => console.info(message, meta ?? {}),
    warn: (message: string, meta?: unknown) => console.warn(message, meta ?? {}),
  };
  const projection = createGatewayProjection();
  const resolved = resolveWsConfig(options);
  const wsUrl = resolved.wsUrl;
  const debug = resolved.debug;

  if (!wsUrl) {
    const mockFeed = options.startMockFeed ?? startMockFeed;
    const handle = mockFeed();
    return { stop: handle.stop, mode: "mock" };
  }

  let closed = false;
  let ws: WebSocket | null = null;
  let backoff = 100;
  let reconnectTimer: number | null = null;

  useCommandCenterStore.getState().setHealth({ wsConnected: false });

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
      try {
        ws?.send(JSON.stringify({ kind: "subscribe", topics: ALL_GATEWAY_TOPICS }));
      } catch {
        /* noop */
      }
      if (debug) {
        logger.info("[command-center] ws connected", {
          url: sanitizeUrl(wsUrl),
          topics: ALL_GATEWAY_TOPICS,
        });
      }
    });
    ws.addEventListener("message", (evt) => {
      try {
        const frame = JSON.parse(String(evt.data)) as IncomingFrame;
        const store = useCommandCenterStore.getState();
        if (frame.kind === "snapshot") {
          const update = projection.applySnapshot(frame.data);
          store.applySnapshot(update.snapshot);
          if (debug) {
            logger.info("[command-center] snapshot applied", {
              signals: update.snapshot.signalOrder?.length ?? 0,
              watchlist: update.snapshot.watchlistOrder?.length ?? 0,
              execEvents: update.snapshot.triggers?.length ?? 0,
            });
            for (const transition of update.execTransitions) {
              logger.info("[command-center] exec status applied", transition);
            }
          }
        } else if (frame.kind === "patch") {
          const update = projection.applyPatch(frame.topic, frame.patch);
          store.applySnapshot(update.snapshot);
          if (debug) {
            logger.info("[command-center] patch applied", {
              topic: frame.topic,
              ops: frame.patch.length,
            });
            for (const transition of update.execTransitions) {
              logger.info("[command-center] exec status applied", transition);
            }
          }
        } else if (frame.kind === "ping") {
          try {
            ws?.send(JSON.stringify({ kind: "pong", ts: frame.ts }));
          } catch {
            /* noop */
          }
        } else if (frame.kind === "error") {
          logger.warn("[command-center] gateway error", {
            code: frame.code,
            message: frame.message,
          });
        }
      } catch (error) {
        logger.warn("[command-center] malformed frame ignored", {
          error: error instanceof Error ? error.message : String(error),
        });
      }
    });
    ws.addEventListener("close", () => {
      useCommandCenterStore.getState().setHealth({ wsConnected: false });
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
      try {
        ws?.close();
      } catch {
        /* noop */
      }
    },
    mode: "ws",
  };
}
