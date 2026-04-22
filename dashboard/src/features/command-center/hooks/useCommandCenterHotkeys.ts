import { useCallback, useEffect } from "react";
import { useHotkeys } from "react-hotkeys-hook";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Toast } from "./useToasts";

interface Options {
  pushToast: (t: Omit<Toast, "id"> & { ttl?: number }) => void;
}

function focusedTag(): string {
  const el = typeof document !== "undefined" ? document.activeElement : null;
  if (!el) return "";
  const tag = el.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return tag;
  if ((el as HTMLElement).getAttribute?.("data-keyboard-safe") === "true") return "SAFE";
  return "";
}

export function useCommandCenterHotkeys({ pushToast }: Options) {
  const moveCursor = useCommandCenterStore((s) => s.moveCursor);
  const setLocalDecision = useCommandCenterStore((s) => s.setLocalDecision);
  const setDetailOpen = useCommandCenterStore((s) => s.setDetailOpen);
  const setKillModalOpen = useCommandCenterStore((s) => s.setKillModalOpen);
  const setHelpOpen = useCommandCenterStore((s) => s.setHelpOpen);
  const togglePerfHud = useCommandCenterStore((s) => s.togglePerfHud);

  const currentSignal = useCallback(() => {
    const s = useCommandCenterStore.getState();
    const id = s.signalOrder[s.cursor.triageIndex];
    return id ? s.signals[id] : null;
  }, []);

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
      setLocalDecision(sig.id, "accept");
      pushToast({
        message: `(Phase 1) accept ${sig.market} recorded locally, not sent to backend`,
        variant: "accept",
      });
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "r",
    () => {
      if (!guard()) return;
      const sig = currentSignal();
      if (!sig) return;
      setLocalDecision(sig.id, "reject");
      pushToast({
        message: `(Phase 1) reject ${sig.market} recorded locally, not sent to backend`,
        variant: "reject",
      });
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "s",
    () => {
      if (!guard()) return;
      const sig = currentSignal();
      if (!sig) return;
      setLocalDecision(sig.id, "snooze");
      pushToast({
        message: `(Phase 1) snooze ${sig.market} recorded locally, not sent to backend`,
        variant: "snooze",
      });
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
      pushToast({ message: "(Phase 1) accept-all recorded locally", variant: "accept" });
    },
    { enableOnFormTags: false },
  );
  useHotkeys(
    "shift+d",
    () => {
      if (!guard()) return;
      pushToast({ message: "(Phase 1) dismiss-all recorded locally", variant: "warn" });
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
