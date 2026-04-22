import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useShallow } from "zustand/react/shallow";
import { useCommandCenterStore } from "../store/useCommandCenterStore";
import type { Signal } from "../store/types";

// Minimal viridis LUT — 10 samples, linearly interpolated between.
const VIRIDIS_STOPS: [number, number, number][] = [
  [68, 1, 84],
  [72, 40, 120],
  [62, 73, 137],
  [49, 104, 142],
  [38, 130, 142],
  [31, 158, 137],
  [53, 183, 121],
  [110, 206, 88],
  [181, 222, 43],
  [253, 231, 37],
];

function viridis(t: number): string {
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (VIRIDIS_STOPS.length - 1);
  const lo = Math.floor(scaled);
  const hi = Math.min(VIRIDIS_STOPS.length - 1, lo + 1);
  const f = scaled - lo;
  const a = VIRIDIS_STOPS[lo]!;
  const b = VIRIDIS_STOPS[hi]!;
  const r = Math.round(a[0] + (b[0] - a[0]) * f);
  const g = Math.round(a[1] + (b[1] - a[1]) * f);
  const bl = Math.round(a[2] + (b[2] - a[2]) * f);
  return `rgb(${r},${g},${bl})`;
}

interface Point {
  signal: Signal;
  x: number; // px
  y: number; // px
  r: number; // px
  colorT: number;
}

export const EdgeHeatmap = memo(function EdgeHeatmap() {
  const signalArr = useCommandCenterStore(
    useShallow((s) => s.signalOrder.map((id) => s.signals[id]!)),
  );
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const pointsRef = useRef<Point[]>([]);
  const [tip, setTip] = useState<{ x: number; y: number; s: Signal } | null>(null);

  const bounds = useMemo(() => {
    if (signalArr.length === 0) return { minE: 0.001, maxE: 0.3, minL: 1_000, maxL: 100_000 };
    const absEdges = signalArr.map((s) => Math.abs(s.edge)).map((v) => Math.max(0.001, v));
    const liqs = signalArr.map((s) => s.liquidityUsd);
    return {
      minE: Math.min(...absEdges),
      maxE: Math.max(...absEdges),
      minL: Math.min(...liqs),
      maxL: Math.max(...liqs),
    };
  }, [signalArr]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const dpr = window.devicePixelRatio || 1;
    const width = wrap.clientWidth;
    const height = wrap.clientHeight;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    let ctx: CanvasRenderingContext2D | null = null;
    try {
      ctx = canvas.getContext("2d");
    } catch {
      // jsdom and other headless environments may not implement canvas.
      ctx = null;
    }
    if (!ctx) return;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, width, height);

    // Axes
    ctx.strokeStyle = "#1f2730";
    ctx.lineWidth = 1;
    ctx.strokeRect(28, 6, width - 38, height - 28);

    ctx.fillStyle = "#5e6b7a";
    ctx.font = '10px "JetBrains Mono", monospace';
    ctx.fillText("log|edge| →", 32, height - 8);
    ctx.save();
    ctx.translate(10, height / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText("liquidity $ →", 0, 0);
    ctx.restore();

    const logMinE = Math.log(bounds.minE);
    const logMaxE = Math.log(bounds.maxE);
    const logRangeE = Math.max(1e-6, logMaxE - logMinE);
    const liqRange = Math.max(1, bounds.maxL - bounds.minL);

    const padL = 32;
    const padR = width - 10;
    const padT = 10;
    const padB = height - 24;

    const pts: Point[] = [];
    for (const s of signalArr) {
      const absEdge = Math.max(bounds.minE, Math.abs(s.edge));
      const xRatio = (Math.log(absEdge) - logMinE) / logRangeE;
      const yRatio = (s.liquidityUsd - bounds.minL) / liqRange;
      const x = padL + xRatio * (padR - padL);
      const y = padB - yRatio * (padB - padT);
      const r = 3 + Math.min(10, Math.sqrt(s.positionSuggested) * 0.3);
      const colorT = Math.max(0, Math.min(1, s.confidence));
      pts.push({ signal: s, x, y, r, colorT });
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = viridis(colorT);
      ctx.globalAlpha = 0.85;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.strokeStyle = "#0b0d10";
      ctx.lineWidth = 1;
      ctx.stroke();
    }
    pointsRef.current = pts;
  }, [signalArr, bounds]);

  return (
    <div className="cc-panel cc-area-heat" aria-label="Edge heatmap">
      <div className="cc-panel-header">
        <span>Edge Heatmap</span>
        <span className="cc-shortcut-hint">
          X log|edge| · Y liquidity · color=confidence · size=pos
        </span>
      </div>
      <div
        className="cc-panel-body cc-panel-body--no-scroll"
        ref={wrapRef}
        style={{ position: "relative" }}
        onMouseMove={(e) => {
          const wrap = wrapRef.current;
          if (!wrap) return;
          const rect = wrap.getBoundingClientRect();
          const mx = e.clientX - rect.left;
          const my = e.clientY - rect.top;
          let hit: Point | null = null;
          for (const p of pointsRef.current) {
            const dx = p.x - mx;
            const dy = p.y - my;
            if (dx * dx + dy * dy <= (p.r + 2) ** 2) {
              hit = p;
              break;
            }
          }
          if (hit) setTip({ x: mx + 10, y: my + 10, s: hit.signal });
          else setTip(null);
        }}
        onMouseLeave={() => setTip(null)}
      >
        <div className="cc-heatmap">
          <canvas ref={canvasRef} />
        </div>
        {tip && (
          <div className="cc-heatmap-tip" style={{ left: tip.x, top: tip.y }}>
            <div style={{ color: "var(--cc-info)", marginBottom: 2 }}>{tip.s.market}</div>
            <div>{tip.s.question}</div>
            <div style={{ color: "var(--cc-text-dim)", marginTop: 4 }}>
              edge {(tip.s.edge * 100).toFixed(1)}% · liq ${Math.round(tip.s.liquidityUsd / 1000)}k · conf{" "}
              {Math.round(tip.s.confidence * 100)}%
            </div>
          </div>
        )}
      </div>
    </div>
  );
});
