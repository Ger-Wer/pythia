import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

export const MarketDetailModal = memo(function MarketDetailModal() {
  const { open, signal, close } = useCommandCenterStore(
    useShallow((s) => ({
      open: s.detailOpen,
      signal: s.signalOrder[s.cursor.triageIndex]
        ? s.signals[s.signalOrder[s.cursor.triageIndex]!]
        : undefined,
      close: s.setDetailOpen,
    })),
  );
  if (!open || !signal) return null;
  return (
    <div className="cc-modal-backdrop" role="dialog" aria-modal="true" aria-label="Market detail">
      <div className="cc-modal">
        <div className="cc-modal-header">
          <div>
            <div style={{ color: "var(--cc-info)", fontSize: 11, letterSpacing: "0.1em", textTransform: "uppercase" }}>
              {signal.market}
            </div>
            <div style={{ fontFamily: "Inter, sans-serif", fontSize: 14 }}>{signal.question}</div>
          </div>
          <button
            type="button"
            className="cc-copy-btn"
            onClick={() => close(false)}
            aria-label="Close market detail"
          >
            Close (Space)
          </button>
        </div>
        <div className="cc-modal-body">
          <div className="cc-modal-section">
            <h4>LLM Rationale</h4>
            <p>{signal.rationale}</p>
          </div>
          <div className="cc-modal-section">
            <h4>Metrics</h4>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 }}>
              <div>
                Edge{" "}
                <strong style={{ color: signal.edge >= 0 ? "var(--cc-ok)" : "var(--cc-crit)" }}>
                  {(signal.edge * 100).toFixed(1)}%
                </strong>
              </div>
              <div>Conf <strong>{Math.round(signal.confidence * 100)}%</strong></div>
              <div>Liq <strong>${Math.round(signal.liquidityUsd / 1000)}k</strong></div>
              <div>Pos <strong>${signal.positionSuggested}</strong></div>
            </div>
          </div>
          <div className="cc-modal-section">
            <h4>Evidence</h4>
            <ul>
              {signal.evidence.map((url) => (
                <li key={url}>
                  <a href={url} target="_blank" rel="noreferrer" style={{ color: "var(--cc-info)" }}>
                    {url}
                  </a>
                </li>
              ))}
            </ul>
          </div>
          <div className="cc-modal-section">
            <h4>Order Book Depth (placeholder)</h4>
            <div
              style={{
                height: 80,
                background: "var(--cc-panel-soft)",
                border: "1px dashed var(--cc-border-strong)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "var(--cc-text-faint)",
              }}
            >
              depth graph (Phase 2)
            </div>
          </div>
          <div className="cc-modal-section">
            <h4>News Timeline (placeholder)</h4>
            <div style={{ color: "var(--cc-text-faint)" }}>timeline wired from gateway in Phase 2</div>
          </div>
        </div>
      </div>
    </div>
  );
});
