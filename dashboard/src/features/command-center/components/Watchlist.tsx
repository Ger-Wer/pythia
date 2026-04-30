import { memo, useRef, type CSSProperties } from "react";
import { useShallow } from "zustand/react/shallow";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { WatchlistRow } from "../store/types";

function edgeClass(edge: number): string {
  if (edge > 0.005) return "cc-num--pos";
  if (edge < -0.005) return "cc-num--neg";
  return "cc-num--zero";
}

interface RowProps {
  row: WatchlistRow;
  style?: CSSProperties;
}

const WatchRow = memo(function WatchRow({ row, style }: RowProps) {
  const edgePct = (row.edge * 100).toFixed(1);
  const execStatus = row.executionStatus?.toLowerCase();
  return (
    <tr style={style}>
      <td style={{ maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis" }}>{row.question}</td>
      <td>{row.side}</td>
      <td className={edgeClass(row.edge)}>{row.edge >= 0 ? "+" : ""}{edgePct}%</td>
      <td>${Math.round(row.liquidityUsd / 1000)}k</td>
      <td>
        <span className="cc-bar" aria-label="order book depth">
          <span style={{ width: `${Math.round(row.depthPct * 100)}%` }} />
        </span>{" "}
        {Math.round(row.depthPct * 100)}%
      </td>
      <td>{row.slippageBps}bps</td>
      <td>
        <span className={`cc-trigger-pill cc-trigger-pill--${row.trigger}`}>{row.trigger}</span>
      </td>
      <td>
        {row.executionStatus ? (
          <span className={`cc-exec-kind cc-exec-kind--${execStatus}`}>{row.executionStatus}</span>
        ) : (
          <span style={{ color: "var(--cc-text-faint)" }}>-</span>
        )}
      </td>
    </tr>
  );
});

export const Watchlist = memo(function Watchlist() {
  const { order, rows } = useCommandCenterStore(
    useShallow((s) => ({ order: s.watchlistOrder, rows: s.watchlist })),
  );
  const parentRef = useRef<HTMLDivElement>(null);

  const virtualizer = useVirtualizer({
    count: order.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 22,
    overscan: 8,
  });

  return (
    <div className="cc-panel cc-area-watch" aria-label="Active watchlist">
      <div className="cc-panel-header">
        <span>Active Watchlist</span>
        <span className="cc-shortcut-hint">{order.length} markets</span>
      </div>
      <div className="cc-panel-body" ref={parentRef}>
        <table className="cc-watch-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Side</th>
              <th>Edge</th>
              <th>Liq</th>
              <th>Depth</th>
              <th>Slip</th>
              <th>Trigger</th>
              <th>Exec</th>
            </tr>
          </thead>
          <tbody
            style={{
              position: "relative",
              display: "block",
              height: `${virtualizer.getTotalSize()}px`,
            }}
          >
            {virtualizer.getVirtualItems().map((vrow) => {
              const id = order[vrow.index]!;
              const row = rows[id]!;
              return (
                <WatchRow
                  key={id}
                  row={row}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${vrow.start}px)`,
                    display: "table",
                    tableLayout: "fixed",
                  }}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
});
