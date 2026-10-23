import { useId } from "react";

/**
 * NOVARA's signature: the eight-pointed star (khatam) of Islamic geometric art, which is also
 * the "nova" in the name. One shape, used as the logo, the step markers and the background lattice.
 */
export function starPath(cx: number, cy: number, r: number): string {
  const inner = r * 0.7654; // two overlapping squares: inner radius = r * cos(45deg) / cos(22.5deg)
  const pts: string[] = [];
  for (let i = 0; i < 16; i++) {
    const a = (Math.PI / 8) * i - Math.PI / 2;
    const rad = i % 2 === 0 ? r : inner;
    pts.push(`${(cx + rad * Math.cos(a)).toFixed(2)},${(cy + rad * Math.sin(a)).toFixed(2)}`);
  }
  return `M${pts.join("L")}Z`;
}

export function StarMark({ size = 30 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="star-mark">
      <path d={starPath(16, 16, 15.5)} className="star-mark-outer" />
      <path d={starPath(16, 16, 9)} className="star-mark-inner" />
      <circle cx="16" cy="16" r="3" className="star-mark-core" />
    </svg>
  );
}

/** Interlaced star lattice: draws itself in currentColor, so its strength is set by CSS. */
export function StarLattice({ className = "" }: { className?: string }) {
  const id = useId().replace(/:/g, "");
  const s = 56;
  return (
    <svg className={`lattice ${className}`} aria-hidden width="100%" height="100%">
      <defs>
        <pattern id={id} width={s} height={s} patternUnits="userSpaceOnUse">
          <path d={starPath(s / 2, s / 2, 17)} fill="none" stroke="currentColor" strokeWidth="1" />
          <path d={starPath(s / 2, s / 2, 7)} fill="none" stroke="currentColor" strokeWidth="1" />
          {[0, s].map((x) => [0, s].map((y) => <path key={`${x}${y}`} d={starPath(x, y, 10)} fill="none" stroke="currentColor" strokeWidth="1" />))}
          <path d={`M${s / 2},0V${s / 2 - 17}M${s / 2},${s}V${s / 2 + 17}M0,${s / 2}H${s / 2 - 17}M${s},${s / 2}H${s / 2 + 17}`} stroke="currentColor" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill={`url(#${id})`} />
    </svg>
  );
}

/** A step marker for document journeys: done = filled star, current = ringed, upcoming = outline. */
export function StepStar({ state }: { state: "done" | "current" | "todo" }) {
  return (
    <svg width="26" height="26" viewBox="0 0 26 26" aria-hidden className={`step-star step-${state}`}>
      <path d={starPath(13, 13, 11.5)} />
      {state === "done" && <path d="M8.6 13.2l3 3 5.8-6.2" fill="none" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="step-check" />}
      {state === "current" && <circle cx="13" cy="13" r="3.2" className="step-dot" />}
    </svg>
  );
}
