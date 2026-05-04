import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type MouseEvent,
} from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import {
  resolveCommandCenterAuth,
  type CommandCenterAuth,
} from "../api/auth";
import {
  submitKill,
  type KillInput,
  type KillResult,
  type KillScope,
} from "../api/killClient";

type Phase = "idle" | "sending" | "result";

interface Props {
  /** Test hook: bypass `resolveCommandCenterAuth()`. */
  authOverride?: CommandCenterAuth;
  /** Test hook: inject a fetch implementation (e.g. vi.fn). */
  fetchImpl?: typeof fetch;
}

const SCOPES: { value: KillScope; label: string; help: string }[] = [
  {
    value: "GLOBAL",
    label: "GLOBAL",
    help: "Halt every paper-trading flow across all markets.",
  },
  {
    value: "NEW_TRIGGERS",
    label: "NEW_TRIGGERS",
    help: "Stop arming new triggers; let in-flight ones drain.",
  },
  {
    value: "MARKET",
    label: "MARKET",
    help: "Halt all activity for a single market_id.",
  },
];

function expectedPhrase(scope: KillScope, marketId: string): string {
  if (scope === "MARKET") return `KILL ${marketId.trim()}`;
  return `KILL ${scope}`;
}

function describeAck(result: KillResult): {
  variant: "ok" | "warn" | "crit";
  label: string;
  detail: string;
} {
  if (!result.ok) {
    return {
      variant: "crit",
      label: result.kind,
      detail: result.message,
    };
  }
  switch (result.ackStatus) {
    case "COMPLETE":
      return {
        variant: "ok",
        label: "COMPLETE",
        detail: result.ackDetail ?? "Executor confirmed kill drain.",
      };
    case "ACCEPTED":
    case "DRAINING":
      return {
        variant: "warn",
        label: result.ackStatus,
        detail:
          result.ackDetail ??
          "Executor accepted the command; verify drain via Execution panel.",
      };
    case "REJECTED_BAD_SIGNATURE":
    case "REJECTED_REPLAY":
      return {
        variant: "crit",
        label: result.ackStatus,
        detail:
          result.ackDetail ??
          "Executor rejected the command. Verify clock + HMAC secret.",
      };
    case "no_bus":
      return {
        variant: "crit",
        label: "no_bus",
        detail:
          result.ackDetail ??
          "Gateway has no bus connection; kill did not propagate.",
      };
    case "timeout":
    default:
      return {
        variant: "crit",
        label: "timeout",
        detail:
          result.ackDetail ??
          "Executor did not ack within timeout; verify state manually.",
      };
  }
}

function describeError(kind: string): string {
  switch (kind) {
    case "no_auth":
      return "Kill switch not configured (api / token / operator_id / kill_hmac).";
    case "missing_market_id":
      return "Enter a market_id for MARKET scope.";
    case "stale_request":
      return "Clock skew >5s; sync system time and try again.";
    case "invalid_signature":
      return "HMAC mismatch; check VITE_COMMAND_CENTER_KILL_HMAC_SECRET.";
    case "unauthorized":
      return "Auth token expired; re-login.";
    case "forbidden":
      return "operator_id mismatch with token sub claim.";
    case "rate_limited":
      return "Rate limited by gateway; retry shortly.";
    case "network":
      return "Network error reaching gateway.";
    case "server":
      return "Gateway returned 5xx.";
    default:
      return "Unexpected error.";
  }
}

export const KillModal = memo(function KillModal({ authOverride, fetchImpl }: Props = {}) {
  const { open, close } = useCommandCenterStore(
    useShallow((s) => ({ open: s.killModalOpen, close: s.setKillModalOpen })),
  );

  const auth = useMemo<CommandCenterAuth>(
    () => authOverride ?? resolveCommandCenterAuth(),
    [authOverride],
  );

  const [scope, setScope] = useState<KillScope>("GLOBAL");
  const [marketId, setMarketId] = useState<string>("");
  const [reason, setReason] = useState<string>("");
  const [confirmText, setConfirmText] = useState<string>("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<KillResult | null>(null);
  const confirmRef = useRef<HTMLInputElement | null>(null);

  const reset = useCallback(() => {
    setScope("GLOBAL");
    setMarketId("");
    setReason("");
    setConfirmText("");
    setPhase("idle");
    setResult(null);
  }, []);

  useEffect(() => {
    if (!open) reset();
  }, [open, reset]);

  const expected = expectedPhrase(scope, marketId);
  const reasonValid = reason.trim().length >= 8 && reason.trim().length <= 256;
  const marketIdValid = scope !== "MARKET" || marketId.trim().length > 0;
  const phraseMatches = confirmText.trim() === expected.trim() && expected !== "KILL ";
  const submitArmed = reasonValid && marketIdValid && phraseMatches && phase === "idle";

  const fire = useCallback(async () => {
    if (!submitArmed) return;
    setPhase("sending");
    const input: KillInput = {
      scope,
      reason: reason.trim(),
      ...(scope === "MARKET" ? { marketId: marketId.trim() } : {}),
    };
    const r = await submitKill(input, { auth, fetchImpl });
    setResult(r);
    setPhase("result");
  }, [submitArmed, scope, reason, marketId, auth, fetchImpl]);

  const onConfirmKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        void fire();
      }
    },
    [fire],
  );

  const onSubmitClick = useCallback(
    (e: MouseEvent<HTMLButtonElement>) => {
      // Two-key requirement: must hold Ctrl (or Cmd on macOS) while
      // clicking. Plain click is suppressed.
      if (!(e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        return;
      }
      e.preventDefault();
      void fire();
    },
    [fire],
  );

  const onFormSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      // Plain Enter on form must NOT fire — only Ctrl+Enter on the
      // confirm input or Ctrl+click on the submit button.
      e.preventDefault();
    },
    [],
  );

  if (!open) return null;

  const blocked = !auth.killReady;
  const ackInfo = result ? describeAck(result) : null;

  return (
    <div
      className="cc-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Kill switch"
      data-testid="cc-kill-modal"
    >
      <div className="cc-modal cc-kill-modal">
        <div className="cc-modal-header">
          <span style={{ color: "var(--cc-crit)", fontWeight: 600 }}>
            Kill Switch
          </span>
          <button
            type="button"
            className="cc-copy-btn"
            onClick={() => close(false)}
            aria-label="Close kill switch"
            data-keyboard-safe="true"
          >
            Close
          </button>
        </div>
        <div className="cc-modal-body">
          {blocked && (
            <div
              className="cc-kill-banner cc-kill-banner--warn"
              role="status"
              data-testid="cc-kill-blocked-banner"
            >
              Kill Switch is read-only — set
              {" "}
              <code>VITE_COMMAND_CENTER_KILL_HMAC_SECRET</code> (or
              {" "}
              <code>?kill_hmac=</code>) plus a valid PASETO token /
              operator_id to enable.
            </div>
          )}

          <form onSubmit={onFormSubmit} className="cc-kill-form">
            <fieldset
              className="cc-modal-section"
              disabled={phase !== "idle"}
              aria-disabled={phase !== "idle"}
            >
              <h4>Scope</h4>
              <div className="cc-kill-scope-row" role="radiogroup" aria-label="Kill scope">
                {SCOPES.map((s) => (
                  <label key={s.value} className="cc-kill-scope-option">
                    <input
                      type="radio"
                      name="cc-kill-scope"
                      value={s.value}
                      checked={scope === s.value}
                      onChange={() => {
                        setScope(s.value);
                        setConfirmText("");
                      }}
                      data-keyboard-safe="true"
                    />
                    <span className="cc-kill-scope-label">{s.label}</span>
                    <span className="cc-kill-scope-help">{s.help}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            {scope === "MARKET" && (
              <fieldset
                className="cc-modal-section"
                disabled={phase !== "idle"}
                aria-disabled={phase !== "idle"}
              >
                <h4>Market ID</h4>
                <input
                  type="text"
                  className="cc-kill-input"
                  value={marketId}
                  onChange={(e) => {
                    setMarketId(e.target.value);
                    setConfirmText("");
                  }}
                  placeholder="poly-…"
                  aria-label="Market ID"
                  data-keyboard-safe="true"
                />
              </fieldset>
            )}

            <fieldset
              className="cc-modal-section"
              disabled={phase !== "idle"}
              aria-disabled={phase !== "idle"}
            >
              <h4>Reason (audited)</h4>
              <textarea
                className="cc-kill-input cc-kill-textarea"
                rows={2}
                maxLength={256}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="8–256 chars; recorded in gateway audit log"
                aria-label="Kill reason"
                data-keyboard-safe="true"
              />
              <div className="cc-kill-meta">
                <span style={{ color: reasonValid ? "var(--cc-text-dim)" : "var(--cc-warn)" }}>
                  {reason.trim().length}/256
                </span>
              </div>
            </fieldset>

            <fieldset
              className="cc-modal-section"
              disabled={phase !== "idle"}
              aria-disabled={phase !== "idle"}
            >
              <h4>
                Confirm — type{" "}
                <code style={{ color: "var(--cc-crit)" }}>{expected}</code> exactly
              </h4>
              <input
                ref={confirmRef}
                type="text"
                className="cc-kill-input"
                value={confirmText}
                onChange={(e) => setConfirmText(e.target.value)}
                onKeyDown={onConfirmKeyDown}
                placeholder={expected}
                aria-label="Confirm phrase"
                data-keyboard-safe="true"
              />
              <div className="cc-kill-meta" style={{ color: "var(--cc-text-dim)" }}>
                Two-key: type the phrase, then{" "}
                <span className="cc-kbd">Ctrl</span>+<span className="cc-kbd">Enter</span> here, or{" "}
                <span className="cc-kbd">Ctrl</span>+click <em>FIRE</em>.
              </div>
            </fieldset>

            <div className="cc-kill-actions">
              <button
                type="button"
                className="cc-kill-fire"
                onClick={onSubmitClick}
                disabled={!submitArmed || blocked}
                aria-disabled={!submitArmed || blocked}
                data-testid="cc-kill-fire"
                data-keyboard-safe="true"
              >
                {phase === "sending" ? "Sending…" : "FIRE"}
              </button>
              <button
                type="button"
                className="cc-copy-btn"
                onClick={() => close(false)}
                data-keyboard-safe="true"
              >
                Cancel
              </button>
            </div>
          </form>

          {ackInfo && (
            <div
              className={`cc-kill-result cc-kill-result--${ackInfo.variant}`}
              role={ackInfo.variant === "crit" ? "alert" : "status"}
              data-testid="cc-kill-result"
            >
              <div className="cc-kill-result-label">{ackInfo.label}</div>
              <div className="cc-kill-result-detail">{ackInfo.detail}</div>
              {result && !result.ok && (
                <div className="cc-kill-result-hint">{describeError(result.kind)}</div>
              )}
              <div className="cc-kill-result-actions">
                <button
                  type="button"
                  className="cc-copy-btn"
                  onClick={reset}
                  data-keyboard-safe="true"
                >
                  Reset form
                </button>
                <button
                  type="button"
                  className="cc-copy-btn"
                  onClick={() => close(false)}
                  data-keyboard-safe="true"
                >
                  Close
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
