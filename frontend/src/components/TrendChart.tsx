import { useState } from "react";
import type { CSSProperties } from "react";
import type { DashboardPoint } from "../api/types";

// ID-HU-FE-004: one ratio over time. A single series (the chart's title names
// it, so no legend), drawn as a 2px line with 8px markers. Only calculated
// values are plotted and joined; a period without one leaves a gap and an
// "n/a" mark. Outliers get the status color *with* a "!" icon and a label,
// never color alone. Hovering a period shows its value and status.

const SERIES = "#2a78d6";
const OUTLIER = "#ec835a"; // status "serious"
const SURFACE = "#ffffff";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";
const MUTED = "#898781";
const INK = "#0b0b0b";
const INK_SECONDARY = "#52514e";

export function formatRatioValue(value: number | null, unit: "percent" | "times"): string {
  if (value == null) return "—";
  return unit === "percent" ? `${(value * 100).toFixed(1)}%` : `${value.toFixed(2)}x`;
}

const STATUS_TEXT: Record<DashboardPoint["status"], string> = {
  calculated: "",
  needs_review: "held for review",
  not_calculable: "not calculable",
  not_calculated: "not calculated",
  restricted: "restricted",
};

// Clean axis ticks: 0 included, a 1/2/2.5/5 x 10^n step.
function niceTicks(min: number, max: number, count = 4): number[] {
  if (min === max) {
    const pad = Math.abs(min) * 0.5 || 1;
    min -= pad;
    max += pad;
  }
  const raw = (max - min) / count;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  const ticks: number[] = [];
  for (let t = Math.floor(min / step) * step; t <= max + step * 1e-9; t += step) {
    ticks.push(Number(t.toFixed(10)));
  }
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

interface TrendChartProps {
  points: DashboardPoint[];
  unit: "percent" | "times";
  title: string;
  // Compact: a sparkline for a card (no axes, no labels, no hover).
  compact?: boolean;
  selectedPeriod?: string | null;
  onSelectPeriod?: (period: string) => void;
}

export default function TrendChart({
  points,
  unit,
  title,
  compact = false,
  selectedPeriod = null,
  onSelectPeriod,
}: TrendChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  // Wide viewBoxes so the text keeps its size when the chart fills a panel.
  const width = compact ? 200 : 1000;
  const height = compact ? 40 : 260;
  const pad = compact
    ? { top: 6, right: 6, bottom: 6, left: 6 }
    : { top: 26, right: 70, bottom: 30, left: 56 };
  const plotted = points.map((p) => (p.status === "calculated" && p.value != null ? p.value : null));
  const values = plotted.filter((v): v is number => v != null);

  if (points.length === 0) return null;
  const ticks = values.length ? niceTicks(Math.min(0, ...values), Math.max(0, ...values)) : [0, 1];
  const yMin = ticks[0];
  const yMax = ticks[ticks.length - 1];
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const x = (i: number) => pad.left + (points.length === 1 ? innerW / 2 : (innerW * i) / (points.length - 1));
  const y = (v: number) => pad.top + innerH - ((v - yMin) / (yMax - yMin || 1)) * innerH;

  // Segments join consecutive calculated values only; a gap stays a gap.
  const segments: string[] = [];
  for (let i = 1; i < points.length; i++) {
    const a = plotted[i - 1];
    const b = plotted[i];
    if (a != null && b != null) segments.push(`M${x(i - 1)},${y(a)} L${x(i)},${y(b)}`);
  }
  const lastIndex = plotted.reduce<number>((last, v, i) => (v != null ? i : last), -1);
  const label = (v: number) => formatRatioValue(v, unit);

  const hovered = hover != null ? points[hover] : null;
  return (
    <figure style={{ margin: 0, position: "relative" }} aria-label={`${title} trend`}>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        width="100%"
        role="img"
        aria-label={`${title}: ${points
          .map((p) => `${p.period} ${p.status === "calculated" ? label(p.value as number) : STATUS_TEXT[p.status]}`)
          .join(", ")}`}
        style={{ display: "block", overflow: "visible", maxHeight: compact ? "2.5rem" : "17rem" }}
      >
        {!compact &&
          ticks.map((t) => (
            <g key={t}>
              <line x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? AXIS : GRID} strokeWidth={1} />
              <text x={pad.left - 8} y={y(t)} textAnchor="end" dominantBaseline="middle" fontSize={11} fill={MUTED} style={tabular}>
                {label(t)}
              </text>
            </g>
          ))}
        {!compact &&
          points.map((p, i) => (
            <text
              key={p.period}
              x={x(i)}
              y={height - 8}
              textAnchor="middle"
              fontSize={11}
              fill={p.period === selectedPeriod ? INK : MUTED}
              fontWeight={p.period === selectedPeriod ? 700 : 400}
            >
              {p.period}
            </text>
          ))}
        {hover != null && !compact && (
          <line x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH} stroke={AXIS} strokeWidth={1} />
        )}
        {segments.map((d) => (
          <path key={d} d={d} fill="none" stroke={SERIES} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" />
        ))}
        {points.map((p, i) => {
          const v = plotted[i];
          if (v == null) {
            if (compact) return null;
            // No value for this period: a muted mark on the baseline, never a fake 0.
            return (
              <text key={p.period} x={x(i)} y={y(Math.max(yMin, Math.min(0, yMax))) - 6} textAnchor="middle" fontSize={10} fill={MUTED}>
                n/a
              </text>
            );
          }
          const outlier = p.outlier != null;
          return (
            <g key={p.period} data-testid={outlier ? `outlier-${p.period}` : undefined}>
              <circle
                cx={x(i)}
                cy={y(v)}
                r={outlier ? (compact ? 4 : 6) : compact ? 2.5 : 4}
                fill={outlier ? OUTLIER : SERIES}
                stroke={SURFACE}
                strokeWidth={2}
              />
              {outlier && !compact && (
                <>
                  <text x={x(i)} y={y(v) + 0.5} textAnchor="middle" dominantBaseline="middle" fontSize={9} fontWeight={700} fill={INK}>
                    !
                  </text>
                  <text x={x(i)} y={y(v) - 13} textAnchor="middle" fontSize={11} fontWeight={600} fill={INK}>
                    Outlier
                  </text>
                </>
              )}
            </g>
          );
        })}
        {!compact && lastIndex >= 0 && (
          <text x={x(lastIndex) + 10} y={y(plotted[lastIndex] as number)} dominantBaseline="middle" fontSize={12} fontWeight={600} fill={INK} style={tabular}>
            {label(plotted[lastIndex] as number)}
          </text>
        )}
        {!compact &&
          points.map((p, i) => {
            const band = points.length === 1 ? innerW : innerW / (points.length - 1);
            return (
              <rect
                key={p.period}
                x={x(i) - band / 2}
                y={pad.top}
                width={band}
                height={innerH + pad.bottom}
                fill="transparent"
                style={{ cursor: onSelectPeriod ? "pointer" : "default" }}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onClick={() => onSelectPeriod?.(p.period)}
              >
                <title>{p.period}</title>
              </rect>
            );
          })}
      </svg>
      {hovered && hover != null && !compact && (
        <div
          role="tooltip"
          style={{
            ...tooltipStyle,
            left: `${(x(hover) / width) * 100}%`,
            transform: x(hover) > width * 0.6 ? "translateX(-105%)" : "translateX(5%)",
          }}
        >
          <div style={{ fontWeight: 600, color: INK }}>{hovered.period}</div>
          <div style={{ color: INK_SECONDARY, ...tabular }}>
            {hovered.status === "calculated" ? label(hovered.value as number) : STATUS_TEXT[hovered.status]}
          </div>
          {hovered.outlier && <div style={{ color: INK, marginTop: "0.2rem" }}>⚠ Outlier — click to review drivers</div>}
        </div>
      )}
    </figure>
  );
}

const tabular: CSSProperties = { fontVariantNumeric: "tabular-nums" };
const tooltipStyle: CSSProperties = {
  position: "absolute",
  top: "0.5rem",
  pointerEvents: "none",
  background: "white",
  border: "1px solid rgba(11,11,11,0.10)",
  borderRadius: "0.4rem",
  boxShadow: "0 2px 8px rgba(0,0,0,0.08)",
  padding: "0.35rem 0.55rem",
  fontSize: "0.78rem",
  whiteSpace: "nowrap",
};
