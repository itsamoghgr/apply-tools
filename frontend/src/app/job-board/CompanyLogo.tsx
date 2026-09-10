"use client";

import { useState } from "react";

// Deterministic lettermark colours, so a company keeps the same badge across
// renders and matches the alert email's treatment.
const MARK_CLASSES = [
  "bg-primary/15 text-primary",
  "bg-info/15 text-info",
  "bg-success/15 text-success",
  "bg-warning/20 text-warning",
  "bg-error/15 text-error",
];

function markClass(name: string) {
  const sum = [...name].reduce((acc, ch) => acc + ch.charCodeAt(0), 0);
  return MARK_CLASSES[sum % MARK_CLASSES.length];
}

/**
 * Company logo with a graceful fallback chain:
 *   explicit logoUrl → DuckDuckGo favicon → Google favicon → coloured lettermark.
 *
 * NOT Clearbit: logo.clearbit.com no longer resolves (the free logo API was
 * retired), so every request failed. The favicon services below were verified
 * live. Each step falls through on error, so a company with no recognisable
 * icon still renders as a lettermark rather than a broken image.
 */
function sourcesFor(domain?: string | null, logoUrl?: string | null): string[] {
  const list: string[] = [];
  if (logoUrl) list.push(logoUrl);
  if (domain) {
    list.push(`https://icons.duckduckgo.com/ip3/${domain}.ico`);
    list.push(`https://www.google.com/s2/favicons?domain=${domain}&sz=64`);
  }
  return list;
}

export default function CompanyLogo({
  name,
  domain,
  logoUrl,
  size = 36,
}: {
  name: string;
  domain?: string | null;
  logoUrl?: string | null;
  size?: number;
}) {
  const sources = sourcesFor(domain, logoUrl);
  // Index into `sources`; advancing past the end renders the lettermark.
  const [attempt, setAttempt] = useState(0);
  const src = sources[attempt] ?? null;
  const failed = src === null;
  const dimension = { width: size, height: size };

  if (!src || failed) {
    return (
      <div
        style={dimension}
        className={`flex shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${markClass(name)}`}
        aria-hidden
      >
        {name.slice(0, 1).toUpperCase()}
      </div>
    );
  }

  return (
    // Deliberately a plain <img>, not next/image: the source is an arbitrary
    // remote logo host that 404s often, and we need the onError fallback to the
    // lettermark. next/image would need every domain allow-listed up front.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={src}
      src={src}
      alt=""
      style={dimension}
      onError={() => setAttempt((n) => n + 1)}
      className="shrink-0 rounded-lg bg-base-200 object-contain"
    />
  );
}
