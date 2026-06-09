// Color-coded confidence badge for a 0–1 verification score.
//   ≥ 0.66  green   (high)
//   ≥ 0.40  amber   (medium)
//   < 0.40  red     (low)
// Uses inline color-mix tokens so it tracks the theme (light/applydark) like
// the existing mode-badge styles in globals.css.

export function confidenceTier(c: number): "high" | "medium" | "low" {
  if (c >= 0.66) return "high";
  if (c >= 0.4) return "medium";
  return "low";
}

const TIER_STYLE: Record<string, { color: string; label: string }> = {
  high: { color: "#3f7a5e", label: "High" },
  medium: { color: "#b87f3c", label: "Medium" },
  low: { color: "#c0504d", label: "Low" },
};

export default function ConfidenceBadge({
  value,
  showLabel = false,
}: {
  value: number | null;
  showLabel?: boolean;
}) {
  if (value === null || Number.isNaN(value)) {
    return <span className="text-xs opacity-40">—</span>;
  }
  const tier = confidenceTier(value);
  const { color, label } = TIER_STYLE[tier];
  const pct = Math.round(value * 100);

  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium tabular-nums"
      style={{
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        color,
        borderColor: `color-mix(in oklab, ${color} 28%, transparent)`,
      }}
      title={`Verification confidence: ${pct}% (${label})`}
    >
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: color }}
        aria-hidden
      />
      {pct}%{showLabel ? ` · ${label}` : ""}
    </span>
  );
}
