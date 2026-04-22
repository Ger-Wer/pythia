import { memo, useEffect, useRef } from "react";
import { useShallow } from "zustand/react/shallow";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Signal } from "../store/types";

function formatEdge(edge: number): string {
  const pct = Math.round(edge * 1000) / 10;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function formatCountdown(ms: number): string {
  if (ms <= 0) return "expired";
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}m${rem.toString().padStart(2, "0")}`;
}

function edgeClass(edge: number): string {
  if (edge > 0.005) return "cc-num--pos";
  if (edge < -0.005) return "cc-num--neg";
  return "cc-num--zero";
}

interface RowProps {
  signal: Signal;
  selected: boolean;
  now: number;
  onClick: (index: number) => void;
  index: number;
}

const TriageRow = memo(function TriageRow({ signal, selected, now, onClick, index }: RowProps) {
  const decisionClass =
    signal.localDecision === "accept"
      ? "is-decided-accept"
      : signal.localDecision === "reject"
        ? "is-decided-reject"
        : signal.localDecision === "snooze"
          ? "is-decided-snooze"
          : "";
  return (
    <div
      className={`cc-triage-row ${selected ? "is-selected" : ""} ${decisionClass}`}
      role="option"
      aria-selected={selected}
      data-testid={selected ? "triage-selected" : undefined}
      onClick={() => onClick(index)}
    >
      <span className={`cc-badge cc-badge--${signal.priority.toLowerCase()}`}>{signal.priority}</span>
      <span className={`cc-triage-meta ${edgeClass(signal.edge)}`}>{formatEdge(signal.edge)}</span>
      <span className="cc-triage-question">{signal.question}</span>
      <span className="cc-triage-meta">${(signal.liquidityUsd / 1000).toFixed(0)}k</span>
      <span className="cc-triage-expiry">{formatCountdown(signal.expiresAt - now)}</span>
    </div>
  );
});

export const TriageQueue = memo(function TriageQueue() {
  const { signalOrder, signals, cursor, now, setCursor } = useCommandCenterStore(
    useShallow((s) => ({
      signalOrder: s.signalOrder,
      signals: s.signals,
      cursor: s.cursor,
      now: s.now,
      setCursor: s.setCursor,
    })),
  );

  const parentRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: signalOrder.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 26,
    overscan: 8,
  });

  // Keep selected row visible while cursor moves.
  useEffect(() => {
    if (signalOrder.length === 0) return;
    rowVirtualizer.scrollToIndex(cursor.triageIndex, { align: "auto" });
  }, [cursor.triageIndex, signalOrder.length, rowVirtualizer]);

  return (
    <div className="cc-panel cc-area-triage" aria-label="Triage queue" role="listbox" tabIndex={0}>
      <div className="cc-panel-header">
        <span>Triage Queue</span>
        <span className="cc-shortcut-hint">
          <span className="cc-kbd">j</span>/<span className="cc-kbd">k</span> nav · <span className="cc-kbd">A</span>/
          <span className="cc-kbd">R</span>/<span className="cc-kbd">S</span>
        </span>
      </div>
      <div className="cc-panel-body" ref={parentRef} data-testid="triage-scroll">
        {signalOrder.length === 0 ? (
          <div style={{ padding: 8, color: "var(--cc-text-faint)" }}>No pending signals.</div>
        ) : (
          <div
            style={{
              height: `${rowVirtualizer.getTotalSize()}px`,
              width: "100%",
              position: "relative",
            }}
          >
            {rowVirtualizer.getVirtualItems().map((vrow) => {
              const id = signalOrder[vrow.index]!;
              const sig = signals[id]!;
              return (
                <div
                  key={id}
                  data-index={vrow.index}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    height: `${vrow.size}px`,
                    transform: `translateY(${vrow.start}px)`,
                  }}
                >
                  <TriageRow
                    signal={sig}
                    selected={vrow.index === cursor.triageIndex}
                    now={now}
                    onClick={setCursor}
                    index={vrow.index}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
});
