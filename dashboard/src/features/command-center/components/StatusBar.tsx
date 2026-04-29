import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

function pingClass(ms: number): string {
  if (ms < 10) return "cc-dot cc-dot--ok";
  if (ms < 30) return "cc-dot cc-dot--warn";
  return "cc-dot cc-dot--crit";
}

export const StatusBar = memo(function StatusBar() {
  const hasRuntimeWs =
    typeof window !== "undefined" &&
    new URLSearchParams(window.location.search).has("cc_ws_url");
  const { health, feedMode } = useCommandCenterStore(
    useShallow((s) => ({
      health: s.health,
      feedMode:
        hasRuntimeWs ||
        (import.meta.env.VITE_COMMAND_CENTER_WS_URL as string | undefined)?.trim()
          ? "WS"
          : "MOCK",
    })),
  );
  return (
    <div className="cc-banner" role="region" aria-label="System status banner">
      <span className="cc-banner-brand">PYTHIA // COMMAND CENTER</span>
      <span className="cc-banner-item">
        <span className={pingClass(health.redisPingMs)} aria-hidden="true" />
        Redis <strong>{health.redisPingMs}ms</strong>
      </span>
      <span className="cc-banner-item">
        <span className={pingClass(health.natsPingMs)} aria-hidden="true" />
        NATS <strong>{health.natsPingMs}ms</strong>
      </span>
      <span className="cc-banner-item">
        <span className={pingClass(health.polygonPingMs)} aria-hidden="true" />
        Polygon <strong>{health.polygonPingMs}ms</strong>
      </span>
      <span className="cc-banner-item">
        LLM budget <strong>{Math.round(health.llmBudgetRemaining * 100)}%</strong>
      </span>
      <span className="cc-banner-item">
        Op TTD <strong>{health.operatorTimeToDecisionSec}s</strong>
      </span>
      <span className="cc-banner-item">
        Feed <strong>{feedMode}</strong>
      </span>
      <span className="cc-banner-item" style={{ marginLeft: "auto" }}>
        Kill{" "}
        <strong style={{ color: health.killSwitchEnabled ? "var(--cc-crit)" : "var(--cc-text-faint)" }}>
          {health.killSwitchEnabled ? "ENGAGED" : "DISABLED (P1)"}
        </strong>
      </span>
      <span className="cc-banner-item">
        WS{" "}
        <span className={health.wsConnected ? "cc-dot cc-dot--ok" : "cc-dot cc-dot--crit"} aria-hidden="true" />
      </span>
    </div>
  );
});
