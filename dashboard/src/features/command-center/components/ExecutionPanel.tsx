import { memo, useCallback } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { TriggerEvent } from "../store/types";

function truncateHash(hash: string): string {
  return `${hash.slice(0, 6)}…${hash.slice(-4)}`;
}

function formatRelative(ms: number, now: number): string {
  const delta = Math.max(0, now - ms);
  if (delta < 1000) return "now";
  if (delta < 60_000) return `${Math.floor(delta / 1000)}s ago`;
  if (delta < 3_600_000) return `${Math.floor(delta / 60_000)}m ago`;
  return `${Math.floor(delta / 3_600_000)}h ago`;
}

interface RowProps {
  ev: TriggerEvent;
  now: number;
  onCopy: (hash: string) => void;
}

const ExecRow = memo(function ExecRow({ ev, now, onCopy }: RowProps) {
  return (
    <li className="cc-exec-item">
      <span className={`cc-exec-kind cc-exec-kind--${ev.kind}`}>{ev.kind}</span>
      <span title={new Date(ev.at).toISOString()}>{formatRelative(ev.at, now)}</span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {ev.market} · {ev.txHash ? truncateHash(ev.txHash) : ev.reason ?? "—"}
        {ev.realizedPrice != null ? ` · fill ${(ev.realizedPrice * 100).toFixed(1)}%` : ""}
        {ev.gasGwei != null ? ` · gas ${ev.gasGwei}gwei` : ""}
      </span>
      {ev.txHash ? (
        <button
          type="button"
          className="cc-copy-btn"
          onClick={() => onCopy(ev.txHash!)}
          aria-label={`Copy transaction hash ${ev.txHash}`}
        >
          copy
        </button>
      ) : (
        <span />
      )}
    </li>
  );
});

interface Props {
  onToast?: (msg: string) => void;
}

export const ExecutionPanel = memo(function ExecutionPanel({ onToast }: Props) {
  const { triggers, now } = useCommandCenterStore(
    useShallow((s) => ({ triggers: s.triggers, now: s.now })),
  );

  const onCopy = useCallback(
    (hash: string) => {
      navigator.clipboard?.writeText(hash).catch(() => undefined);
      onToast?.(`Copied ${hash.slice(0, 10)}…`);
    },
    [onToast],
  );

  return (
    <div className="cc-panel cc-area-exec" aria-label="Execution panel">
      <div className="cc-panel-header">
        <span>Execution</span>
        <span className="cc-shortcut-hint">last {triggers.length}</span>
      </div>
      <div className="cc-panel-body">
        {triggers.length === 0 ? (
          <div style={{ padding: 8, color: "var(--cc-text-faint)" }}>No triggers yet.</div>
        ) : (
          <ul className="cc-exec-list">
            {triggers.map((ev) => (
              <ExecRow key={ev.id} ev={ev} now={now} onCopy={onCopy} />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
});
