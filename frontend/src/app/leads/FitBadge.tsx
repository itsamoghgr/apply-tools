// Color-coded ICP fit badge for a 0–1 fit score from the deep-research gate.
//   ≥ 0.66  green   (strong fit)
//   ≥ 0.40  amber   (partial fit)
//   < 0.40  red     (weak fit)
// Mirrors ConfidenceBadge's theme-tracking color-mix styling; differs only in
// semantics (ICP fit, not verification) and surfaces `fitReason` as a tooltip.

function fitTier(c: number): "high" | "medium" | "low" {
  if (c >= 0.66) return "high";
  if (c >= 0.4) return "medium";
  return "low";
}

const TIER_STYLE: Record<string, { color: string; label: string }> = {
  high: { color: "#3f7a5e", label: "Strong" },
  medium: { color: "#b87f3c", label: "Partial" },
  low: { color: "#c0504d", label: "Weak" },
};

export default function FitBadge({
  value,
  reason,
}: {
  value: number | null;
  reason?: string | null;
}) {
  if (value === null || Number.isNaN(value)) {
    return <span className="text-xs opacity-40">—</span>;
  }
  const { color, label } = TIER_STYLE[fitTier(value)];
  const pct = Math.round(value * 100);

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium tabular-nums"
      style={{
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        color,
        borderColor: `color-mix(in oklab, ${color} 28%, transparent)`,
      }}
      title={reason ? `Fit ${pct}% (${label}) — ${reason}` : `ICP fit: ${pct}% (${label})`}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: color }}
        aria-hidden
      />
      {pct}%
    </span>
  );
}
