"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Settings2, X, History, GraduationCap, Globe } from "lucide-react";
import { setRetentionWeeks, setMaxYears, setGlobalCountries } from "./actions";
import { COUNTRY_OPTIONS } from "./constants";

/**
 * Scrape-wide settings — the ones that apply to every watched company.
 *
 * Both are non-destructive: postings outside a window or above an experience
 * ceiling are ARCHIVED, not deleted, so widening either setting brings them
 * straight back. That is what makes them safe to experiment with, and why
 * there is no "are you sure?" prompt.
 */
export default function GlobalSettings({
  retentionWeeks,
  maxYears,
  countries,
  liveCount,
}: {
  retentionWeeks: number;
  maxYears: number;
  countries: string[];
  liveCount: number;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function save(fn: () => Promise<{ error?: string; message?: string }>) {
    setBusy(true);
    try {
      const res = await fn();
      if (res.error) toast.error(res.error);
      else toast.success(res.message ?? "Saved.");
      startTransition(() => router.refresh());
    } finally {
      setBusy(false);
    }
  }

  const summary = [
    `${retentionWeeks}w`,
    maxYears ? `≤${maxYears}y` : "any exp",
    countries.length ? countries.slice(0, 2).join("/") + (countries.length > 2 ? "+" : "") : "worldwide",
  ].join(" · ");

  function toggleCountry(code: string) {
    const next = countries.includes(code)
      ? countries.filter((c) => c !== code)
      : [...countries, code];
    save(() => setGlobalCountries(next));
  }

  return (
    <div className="relative">
      <button
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-base-300 bg-base-100 px-2.5 text-xs transition-colors hover:border-primary"
        onClick={() => setOpen(!open)}
        title="Scrape settings that apply to every company"
      >
        <Settings2 className="h-3.5 w-3.5 opacity-60" />
        Scrape settings
        <span className="tabular-nums opacity-50">{summary}</span>
      </button>

      {open && (
        <>
          {/* Click-away layer, so the panel closes without a global listener. */}
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-2 w-80 rounded-box border border-base-300 bg-base-100 p-4 shadow-lg">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Scrape settings</h3>
              <button
                className="btn btn-ghost btn-xs btn-square"
                onClick={() => setOpen(false)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            <p className="mb-4 text-xs opacity-60">
              Applies to every watched company. Postings outside these limits are
              hidden, not deleted — widening a setting brings them back.
            </p>

            <div className="space-y-4">
              <label className="block">
                <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                  <History className="h-3.5 w-3.5" />
                  Keep postings from the last
                </span>
                <select
                  className="h-8 w-full rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                  value={String(retentionWeeks)}
                  onChange={(e) => save(() => setRetentionWeeks(Number(e.target.value)))}
                  disabled={busy}
                >
                  <option value="1">1 week</option>
                  <option value="2">2 weeks</option>
                  <option value="3">3 weeks</option>
                  <option value="4">4 weeks</option>
                </select>
              </label>

              <label className="block">
                <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                  <GraduationCap className="h-3.5 w-3.5" />
                  Maximum experience required
                </span>
                <select
                  className="h-8 w-full rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                  value={String(maxYears)}
                  onChange={(e) => save(() => setMaxYears(Number(e.target.value)))}
                  disabled={busy}
                >
                  <option value="0">No limit</option>
                  <option value="2">Up to 2 years</option>
                  <option value="3">Up to 3 years</option>
                  <option value="4">Up to 4 years</option>
                  <option value="5">Up to 5 years</option>
                  <option value="6">Up to 6 years</option>
                  <option value="8">Up to 8 years</option>
                </select>
                <span className="mt-1 block text-[11px] opacity-55">
                  Roles that don&apos;t state a requirement are always kept —
                  unstated doesn&apos;t mean senior.
                </span>
              </label>
            </div>

            <div className="mt-4">
              <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                <Globe className="h-3.5 w-3.5" />
                Countries
                <span className="opacity-60">
                  {countries.length === 0 ? "(anywhere)" : `(${countries.length})`}
                </span>
              </span>
              <div className="flex flex-wrap gap-1">
                {COUNTRY_OPTIONS.map((c) => {
                  const on = countries.includes(c.code);
                  return (
                    <button
                      key={c.code}
                      title={c.name}
                      disabled={busy}
                      onClick={() => toggleCountry(c.code)}
                      className={`inline-flex h-6 items-center rounded-md px-1.5 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                        on ? "bg-primary text-primary-content" : "bg-base-200 hover:bg-base-300"
                      }`}
                    >
                      {c.code}
                    </button>
                  );
                })}
              </div>
              <span className="mt-1 block text-[11px] opacity-55">
                A default — a company with its own country list keeps it.
              </span>
            </div>

            <div className="mt-4 border-t border-base-300 pt-3 text-[11px] opacity-55">
              {liveCount} postings currently shown.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
