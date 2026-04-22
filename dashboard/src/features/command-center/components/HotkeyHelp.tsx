import { memo } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

const ROWS: { keys: string[]; action: string }[] = [
  { keys: ["j", "k"], action: "Navigate Triage Queue" },
  { keys: ["A"], action: "Accept signal (Phase 1: local)" },
  { keys: ["R"], action: "Reject signal (Phase 1: local)" },
  { keys: ["S"], action: "Snooze signal (Phase 1: local)" },
  { keys: ["Space"], action: "Toggle Market Detail modal" },
  { keys: ["Shift", "A"], action: "Accept all pending (Phase 1: toast)" },
  { keys: ["Shift", "D"], action: "Dismiss all pending (Phase 1: toast)" },
  { keys: ["Ctrl", "Shift", "K"], action: "Kill Switch (disabled in Phase 1)" },
  { keys: ["Ctrl", "Shift", "P"], action: "Toggle perf HUD" },
  { keys: ["?"], action: "Toggle this help" },
];

export const HotkeyHelp = memo(function HotkeyHelp() {
  const { open, close } = useCommandCenterStore(
    useShallow((s) => ({ open: s.helpOpen, close: s.setHelpOpen })),
  );
  if (!open) return null;
  return (
    <div className="cc-modal-backdrop" role="dialog" aria-modal="true" aria-label="Hotkey reference">
      <div className="cc-modal" style={{ minWidth: 480 }}>
        <div className="cc-modal-header">
          <span>Hotkeys</span>
          <button type="button" className="cc-copy-btn" onClick={() => close(false)} aria-label="Close hotkey help">
            Close (?)
          </button>
        </div>
        <div className="cc-modal-body">
          <table className="cc-help-table">
            <tbody>
              {ROWS.map((r) => (
                <tr key={r.action}>
                  <td>
                    {r.keys.map((k, i) => (
                      <span key={k}>
                        <span className="cc-kbd">{k}</span>
                        {i < r.keys.length - 1 ? " + " : ""}
                      </span>
                    ))}
                  </td>
                  <td>{r.action}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
});
