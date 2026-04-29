import { memo, useCallback, useEffect, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { StatusBar } from "./components/StatusBar";
import { TriageQueue } from "./components/TriageQueue";
import { Watchlist } from "./components/Watchlist";
import { EdgeHeatmap } from "./components/EdgeHeatmap";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { LatencyHistograms } from "./components/LatencyHistograms";
import { MarketDetailModal } from "./components/MarketDetailModal";
import { KillModal } from "./components/KillModal";
import { HotkeyHelp } from "./components/HotkeyHelp";
import { PerfHud } from "./components/PerfHud";
import { ToastHost } from "./components/ToastHost";
import { useToasts } from "./hooks/useToasts";
import { useCommandCenterHotkeys } from "./hooks/useCommandCenterHotkeys";
import { startCommandCenterFeed, type CommandCenterFeedHandle } from "./ws/client";
import { useCommandCenterStore } from "./store/useCommandCenterStore";
import "./styles.css";

export const DUAL_LOOP_UI_ENABLED =
  (import.meta.env.VITE_DUAL_LOOP_UI_ENABLED as string | undefined) === "true" ||
  (import.meta.env.VITE_DUAL_LOOP_UI_ENABLED as string | undefined) === "1";

function isPreviewOverride(search: URLSearchParams): boolean {
  return search.get("preview") === "1";
}

interface PageProps {
  /** Test hook: skip starting feed; tests control the store directly. */
  disableFeed?: boolean;
}

export const CommandCenterPage = memo(function CommandCenterPage({ disableFeed }: PageProps = {}) {
  const [search] = useSearchParams();
  const preview = isPreviewOverride(search);
  const accessible = DUAL_LOOP_UI_ENABLED || preview;

  const { toasts, push } = useToasts();
  const feedRef = useRef<CommandCenterFeedHandle | null>(null);
  const reset = useCommandCenterStore((s) => s.reset);
  const setNow = useCommandCenterStore((s) => s.setNow);

  useEffect(() => {
    if (!accessible) return;
    setNow(Date.now());
    const timer = window.setInterval(() => {
      setNow(Date.now());
    }, 1000);
    return () => clearInterval(timer);
  }, [accessible, setNow]);

  useEffect(() => {
    if (!accessible) return;
    if (disableFeed) return;
    reset();
    feedRef.current = startCommandCenterFeed();
    return () => {
      feedRef.current?.stop();
      feedRef.current = null;
    };
  }, [accessible, disableFeed, reset]);

  const pushToast = useCallback(
    (t: Parameters<typeof push>[0]) => push(t),
    [push],
  );

  useCommandCenterHotkeys({ pushToast });

  if (!accessible) {
    return (
      <div style={{ padding: 24, fontFamily: "Inter, sans-serif", color: "#17221e" }}>
        <h2>Command Center disabled</h2>
        <p>
          Set <code>VITE_DUAL_LOOP_UI_ENABLED=true</code> in <code>.env</code>, or append{" "}
          <code>?preview=1</code> to the URL to preview.
        </p>
      </div>
    );
  }

  return (
    <div className="cc-root" data-testid="command-center-root">
      {preview && !DUAL_LOOP_UI_ENABLED && (
        <div className="cc-preview-ribbon">Preview mode (flag disabled)</div>
      )}
      <div className="cc-grid">
        <div className="cc-area-banner">
          <StatusBar />
        </div>
        <TriageQueue />
        <Watchlist />
        <ExecutionPanel onToast={(message) => push({ message, variant: "info" })} />
        <EdgeHeatmap />
        <LatencyHistograms />
        <div className="cc-area-status cc-status-strip" role="contentinfo">
          <span>PYTHIA dual-loop · Phase 1 · read-only</span>
          <span>
            <span className="cc-kbd">?</span> help · <span className="cc-kbd">Ctrl</span>+
            <span className="cc-kbd">Shift</span>+<span className="cc-kbd">P</span> perf
          </span>
          <span className="cc-spacer" />
          <span>
            Kill Switch{" "}
            <span style={{ color: "var(--cc-text-faint)" }}>disabled in Phase 1</span>{" "}
            <span className="cc-kbd">Ctrl</span>+<span className="cc-kbd">Shift</span>+
            <span className="cc-kbd">K</span>
          </span>
        </div>
      </div>

      <MarketDetailModal />
      <KillModal />
      <HotkeyHelp />
      <ToastHost toasts={toasts} />
      <PerfHud />
    </div>
  );
});
