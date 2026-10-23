import { useEffect, useRef, useState } from "react";

export type Column = { key: string; label: string; value: string; display: string; detail?: string };

const H = 200; // plot height in px
const PAD_TOP = 22; // room for the extreme's label
const AXIS_W = 48;
const AXIS_H = 26;
const SLOT_MIN = 40;
const BAR_MAX = 24;
const R = 4;

/** Ticks on clean numbers: 1, 2, 2.5 or 5 times a power of ten. */
export function niceTicks(max: number): number[] {
  if (max <= 0) return [0];
  const raw = max / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) ?? raw;
  const ticks = [0];
  while (ticks[ticks.length - 1]! < max) ticks.push(ticks.length * step);
  return ticks;
}

function compact(v: number, locale: string): string {
  return new Intl.NumberFormat(locale, { notation: "compact", maximumFractionDigits: 1 }).format(v);
}

/** Column path: square at the baseline, 4px rounded data-end. */
function columnPath(x: number, y: number, w: number, h: number): string {
  if (h <= 0) return "";
  const r = Math.min(R, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

/**
 * One-series column chart (magnitude over ordered categories), laid out in real pixels so
 * text and marks keep their size at any width. Geometry uses floats; every figure the reader
 * sees is the exact string from the API, and the same data is always available as a table.
 */
export function ColumnChart({ columns, locale, title }: { columns: Column[]; locale: string; title: string }) {
  const wrap = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(640);
  const [active, setActive] = useState<number | null>(null);

  useEffect(() => {
    const el = wrap.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => entry && setAvailable(Math.floor(entry.contentRect.width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const n = Math.max(1, columns.length);
  const slot = Math.max(SLOT_MIN, (available - AXIS_W) / n);
  const width = AXIS_W + n * slot; // wider than the card only when there are many columns: it scrolls
  const bar = Math.min(BAR_MAX, slot - 2);
  const values = columns.map((c) => Math.max(0, Number(c.value)));
  const ticks = niceTicks(Math.max(0, ...values));
  const top = ticks[ticks.length - 1] || 1;
  const y = (v: number) => PAD_TOP + H - (v / top) * H;
  const peak = values.indexOf(Math.max(...values));
  const tip = active !== null ? columns[active] : undefined;

  return (
    <div ref={wrap} className="chart" dir="ltr">
      <div className="chart-scroll">
        <svg width={width} height={PAD_TOP + H + AXIS_H} role="group" aria-label={title} onPointerLeave={() => setActive(null)}>
          {ticks.map((v) => (
            <g key={v}>
              <line x1={AXIS_W} x2={width} y1={y(v)} y2={y(v)} className="chart-grid" />
              <text x={AXIS_W - 8} y={y(v)} dy="0.32em" textAnchor="end" className="chart-tick">
                {compact(v, locale)}
              </text>
            </g>
          ))}
          {columns.map((c, i) => {
            const x = AXIS_W + i * slot;
            const v = values[i]!;
            const yTop = y(v);
            return (
              <g
                key={c.key}
                tabIndex={0}
                role="img"
                aria-label={`${c.label}: ${c.display}`}
                onPointerEnter={() => setActive(i)}
                onFocus={() => setActive(i)}
                onBlur={() => setActive(null)}
                className={active === i ? "chart-col active" : "chart-col"}
              >
                {/* hit target: the whole slot, not just the painted column */}
                <rect x={x} y={PAD_TOP} width={slot} height={H} fill="transparent" />
                <path d={columnPath(x + (slot - bar) / 2, yTop, bar, PAD_TOP + H - yTop)} className="chart-bar" />
                {i === peak && v > 0 && (
                  <text x={x + slot / 2} y={yTop - 6} textAnchor="middle" className="chart-label">
                    {compact(v, locale)}
                  </text>
                )}
                <text x={x + slot / 2} y={PAD_TOP + H + 17} textAnchor="middle" className="chart-tick">
                  {c.label}
                </text>
              </g>
            );
          })}
          <line x1={AXIS_W} x2={width} y1={y(0)} y2={y(0)} className="chart-axis" />
        </svg>
        {tip && active !== null && (
          <div className="chart-tip" role="status" style={{ left: Math.min(Math.max(AXIS_W + active * slot + slot / 2, 70), width - 70) }}>
            <strong className="num">{tip.display}</strong>
            <span className="muted small">
              {tip.label}
              {tip.detail ? ` · ${tip.detail}` : ""}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
