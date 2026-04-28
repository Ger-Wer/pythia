import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

export const KillModal = memo(function KillModal() {
  const { open, close } = useCommandCenterStore(
    useShallow((s) => ({ open: s.killModalOpen, close: s.setKillModalOpen })),
  );
  if (!open) return null;
  return (
    <div className="cc-modal-backdrop" role="dialog" aria-modal="true" aria-label="Kill switch disabled">
      <div className="cc-modal" style={{ minWidth: 420, maxWidth: 520 }}>
        <div className="cc-modal-header">
          <span style={{ color: "var(--cc-crit)" }}>Kill Switch — Disabled</span>
          <button type="button" className="cc-copy-btn" onClick={() => close(false)}>
            Close
          </button>
        </div>
        <div className="cc-modal-body">
          <p>
            The Kill Switch is <strong>disabled in Phase 1</strong>. Live execution wiring lands in Phase 2 via{" "}
            <code>gateway-api</code>, and the two-key confirm flow ships in Phase 3 along with real-capital
            execution.
          </p>
          <p style={{ color: "var(--cc-text-dim)" }}>
            Until then, this hotkey (<span className="cc-kbd">Ctrl</span>+<span className="cc-kbd">Shift</span>+
            <span className="cc-kbd">K</span>) only shows this notice.
          </p>
        </div>
      </div>
    </div>
  );
});
