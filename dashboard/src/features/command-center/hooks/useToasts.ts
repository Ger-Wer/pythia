import { useCallback, useEffect, useRef, useState } from "react";

export interface Toast {
  id: string;
  message: string;
  variant?: "info" | "accept" | "reject" | "snooze" | "warn";
}

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef<Map<string, number>>(new Map());

  const push = useCallback((t: Omit<Toast, "id"> & { id?: string; ttl?: number }) => {
    const id = t.id ?? `t-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    setToasts((prev) => [...prev.slice(-4), { id, message: t.message, variant: t.variant ?? "info" }]);
    const timerId = window.setTimeout(() => {
      setToasts((prev) => prev.filter((x) => x.id !== id));
      timers.current.delete(id);
    }, t.ttl ?? 3500);
    timers.current.set(id, timerId);
  }, []);

  useEffect(
    () => () => {
      for (const id of timers.current.values()) clearTimeout(id);
      timers.current.clear();
    },
    [],
  );

  return { toasts, push } as const;
}
