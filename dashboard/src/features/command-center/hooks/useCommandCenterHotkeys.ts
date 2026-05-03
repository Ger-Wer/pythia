import { useCallback, useEffect, useMemo } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { LocalDecision, Signal } from "../store/types";
import type { Toast } from "./useToasts";
import { resolveCommandCenterAuth, type CommandCenterAuth } from "../api/auth";
import {
  decideAccept,
  decideReject,
  decideSnooze,
  type DecideClientOptions,
  type DecideResult,
} from "../api/decideClient";

interface Options {
  pushToast: (t: Omit<Toast, "id"> & { ttl?: number }) => void;
  /** Test hook: skip the live HTTP path and only do optimistic store updates. */
  disableHttp?: boolean;
  /** Test hook: inject a custom fetch (e.g. vi.fn). */
  fetchImpl?: typeof fetch;
  /** Test hook: inject auth resolver result. */
  authOverride?: CommandCenterAuth;
}

const SNOOZE_DEFAULT_MS = 60 * 60 * 1000; // 1 hour
const REJECT_DEFAULT_REASON = "Operator reject (Command Center hotkey)";

function focusedTag(): string {
  const el = typeof document !== "undefined" ? document.activeElement : null;
  if (!el) return "";
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return tag;
  if ((el as HTMLElement).getAttribute?.("data-keyboard-safe") === "true") return "SAFE";
  return "";
}

function describeError(result: Extract<DecideResult, { ok: false }>): string {
  switch (result.kind) {
    case "no_auth":
      return "no backend auth — recorded locally only";
    case "version_conflict":
      return `version conflict (server v${result.actualVersion ?? "?"}); refresh and retry`;
    case "unauthorized":
      return "auth expired — re-login required";
    case "forbidden":
      return "forbidden — operator_id mismatch";
    case "not_found":
      return "signal no longer exists on server";
    case "rate_limited":
      return "rate limited by gateway";
    case "network":
      return "network error reaching gateway";
    case "server":
      return "gateway 5xx — recorded locally";
    default:
      return `failed: ${result.message}`;
  }
}

export function useCommandCenterHotkeys({ pushToast, disableHttp, fetchImpl, authOverride }: Options) {
  const moveCursor = useCommandCenterStore((s) => s.moveCursor);
  const setLocalDecision = useCommandCenterStore((s) => s.setLocalDecision);
  const setDetailOpen = useCommandCenterStore((s) => s.setDetailOpen);
  const setKillModalOpen = useCommandCenterStore((s) => s.setKillModalOpen);
  const setHelpOpen = useCommandCenterStore((s) => s.setHelpOpen);
  const togglePerfHud = useCommandCenterStore((s) => s.togglePerfHud);

  const auth: CommandCenterAuth = useMemo(
    () => authOverride ?? resolveCommandCenterAuth(),
    [authOverride],
  );

  const clientOptions: DecideClientOptions = useMemo(
    () => ({ auth, fetchImpl }),
    [auth, fetchImpl],
  );

  const currentSignal = useCallback((): Signal | null => {
    const s = useCommandCenterStore.getState();
    const id = s.signalOrder[s.cursor.triageIndex];
    return id ? s.signals[id] ?? null : null;
  }, []);

  // Apply optimistic local decision, fire backend request, roll back on
  // hard error. Soft errors (no_auth, network, server 5xx) keep the
  // optimistic decision so the operator still sees their intent — the
  // authoritative WS update will reconcile on reconnect.
  const submit = useCallback(
    async (
      sig: Signal,
      decision: LocalDecision,
      run: (opts: DecideClientOptions) => Promise<DecideResult>,
    ): Promise<void> => {
      const previous = sig.localDecision;
      setLocalDecision(sig.id, decision);

      if (disableHttp || !decision) return;
      if (!auth.ready) {
        pushToast({
          message: `${decision} ${sig.market} — local only (HTTP auth not configured)`,
          variant: "info",
        });
        return;
      }

      const variantForDecision = decision === "accept" ? "accept" : decision === "reject" ? "reject" : "snooze";
      const result = await run(clientOptions);
      if (result.ok) {
        pushToast({
          message: `${decision} ${sig.market} accepted by gateway`,
          variant: variantForDecision,
        });
        return;
      }

      const detail = describeError(result);
      // Hard rollback only for conflicts the server explicitly rejected.
      const rollback =
        result.kind === "version_conflict" ||
        result.kind === "not_found" ||
        result.kind === "forbidden";
      if (rollback) {
        setLocalDecision(sig.id, previous);
      }
      pushToast({
        message: `${decision} ${sig.market} — ${detail}`,
        variant: "warn",
      });
    },
    [auth, clientOptions, disableHttp, pushToast, setLocalDecision],
  );

  const guard = () => focusedTag() === "";

  useHotkeys(
    "j",
    () => {
      if (!guard()) return;
      moveCursor(1);
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "k",
    () => {
      if (!guard()) return;
      moveCursor(-1);
    },
    { enableOnFormTags: false },
  );

  useHotkeys(
    "a",
    () => {
      if (!guard()) return;
      const sig = currentSignal();
      if (!sig) return;
      void submit(sig, "accept", (opts) =>
        decideAccept({ signalId: sig.id, expectedVersion: sig.serverVersion }, opts),
      );
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "r",
    () => {
      if (!guard()) return;
      const sig = currentSignal();
      if (!sig) return;
      void submit(sig, "reject", (opts) =>
        decideReject(
          {
            signalId: sig.id,
            expectedVersion: sig.serverVersion,
            reason: REJECT_DEFAULT_REASON,
          },
          opts,
        ),
      );
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "s",
    () => {
      if (!guard()) return;
      const sig = currentSignal();
      if (!sig) return;
      const snoozeUntilUnixMs = Date.now() + SNOOZE_DEFAULT_MS;
      void submit(sig, "snooze", (opts) =>
        decideSnooze(
          { signalId: sig.id, expectedVersion: sig.serverVersion, snoozeUntilUnixMs },
          opts,
        ),
      );
    },
    { enableOnFormTags: false },
  );

  useHotkeys(
    "space",
    (e) => {
      if (!guard()) return;
      e.preventDefault();
      const open = useCommandCenterStore.getState().detailOpen;
      setDetailOpen(!open);
    },
    { enableOnFormTags: false, preventDefault: true },
  );

  useHotkeys(
    "shift+a",
    () => {
      if (!guard()) return;
      pushToast({
        message: "accept-all not wired yet — use single-signal A",
        variant: "warn",
      });
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "shift+d",
    () => {
      if (!guard()) return;
      pushToast({
        message: "dismiss-all not wired yet — use single-signal R",
        variant: "warn",
      });
    },
    { enableOnFormTags: false },
  );

  useHotkeys(
    "ctrl+shift+k",
    (e) => {
      if (!guard()) return;
      e.preventDefault();
      setKillModalOpen(true);
    },
    { enableOnFormTags: false, preventDefault: true },
  );

  useHotkeys(
    "ctrl+shift+p",
    (e) => {
      if (!guard()) return;
      e.preventDefault();
      togglePerfHud();
    },
    { enableOnFormTags: false, preventDefault: true },
  );

  // `?` (Shift+/) and Ctrl+/ — implemented via direct keydown listener
  // because `hotkeys-js` key normalisation for `?`/`/` varies by layout.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (focusedTag() !== "") return;
      const isQuestion = e.key === "?" || (e.shiftKey && (e.key === "/" || e.code === "Slash"));
      const isCtrlSlash = e.ctrlKey && (e.key === "/" || e.code === "Slash");
      if (!isQuestion && !isCtrlSlash) return;
      e.preventDefault();
      const open = useCommandCenterStore.getState().helpOpen;
      setHelpOpen(!open);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setHelpOpen]);

  useHotkeys(
    "escape",
    () => {
      const s = useCommandCenterStore.getState();
      if (s.detailOpen) s.setDetailOpen(false);
      if (s.killModalOpen) s.setKillModalOpen(false);
      if (s.helpOpen) s.setHelpOpen(false);
    },
    { enableOnFormTags: true },
  );
}
