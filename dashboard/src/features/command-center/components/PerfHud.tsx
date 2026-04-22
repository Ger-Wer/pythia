import { memo, useEffect, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";

export const PerfHud = memo(function PerfHud() {
  const open = useCommandCenterStore(useShallow((s) => s.perfHudOpen));
  const [fps, setFps] = useState(0);
  const frameCount = useRef(0);
  const lastTick = useRef(performance.now());
  const rafId = useRef<number | null>(null);

  useEffect(() => {
    if (!open) return;
    const loop = () => {
      frameCount.current += 1;
      const now = performance.now();
      if (now - lastTick.current >= 500) {
        setFps(Math.round((frameCount.current * 1000) / (now - lastTick.current)));
        frameCount.current = 0;
        lastTick.current = now;
      }
      rafId.current = requestAnimationFrame(loop);
    };
    rafId.current = requestAnimationFrame(loop);
    return () => {
      if (rafId.current != null) cancelAnimationFrame(rafId.current);
    };
  }, [open]);

  if (!open) return null;
  return (
    <div className="cc-perf-hud" role="status" aria-label="Performance HUD">
      FPS <strong>{fps}</strong> · press <span className="cc-kbd">Ctrl</span>+<span className="cc-kbd">Shift</span>+
      <span className="cc-kbd">P</span> to toggle
    </div>
  );
});
