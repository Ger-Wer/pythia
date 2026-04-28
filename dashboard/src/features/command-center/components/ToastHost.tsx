import { memo } from "react";
import type { Toast } from "../hooks/useToasts";

interface Props {
  toasts: Toast[];
}

export const ToastHost = memo(function ToastHost({ toasts }: Props) {
  if (toasts.length === 0) return null;
  return (
    <div className="cc-toasts" aria-live="polite" aria-atomic="true">
      {toasts.map((t) => (
        <div key={t.id} className={`cc-toast cc-toast--${t.variant ?? "info"}`} role="status">
          {t.message}
        </div>
      ))}
    </div>
  );
});
