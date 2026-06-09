"use client";

import { useState, useMemo, useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import ApplicationsRow from "./ApplicationsRow";

// Total <th> count in the table header below — used to colSpan group
// header rows. Bump this if you add or remove a column.
const COLUMN_COUNT = 11;

// Bucket an applied date into a relative group label, like Gmail's inbox.
// Returns a stable group key (used to sort buckets) plus a human label.
// Buckets, newest first:
//   today, yesterday, earlier-this-week, last-week, earlier-this-month,
//   YYYY-MM (for older months).
//
// IMPORTANT — timezone handling: `appliedDate` is a calendar date the user
// picked, but Prisma serializes DateTime columns as UTC. Date-only values
// land as `2026-05-06T00:00:00.000Z`, which `new Date(...)` then renders
// as the *previous* local day in any TZ west of UTC. To keep the bucket
// match the day the user actually wrote in the Applied column, we compare
// year/month/day in UTC for both `now` and the parsed date. The Applied
// column also displays the UTC date, so the two stay consistent.
function bucketFor(appliedISO: string, now = new Date()): {
  key: string;
  label: string;
  // Higher = more recent; used to sort buckets in the UI.
  rank: number;
} {
  const d = new Date(appliedISO);
  if (isNaN(d.getTime())) {
    return { key: "unknown", label: "Unknown", rank: -1 };
  }

  // Treat both anchor and target as midnight-UTC of their respective
  // calendar days. This makes "Today" mean "the same UTC date as now",
  // which matches what the Applied column shows.
  const utcMidnight = (x: Date) =>
    Date.UTC(x.getUTCFullYear(), x.getUTCMonth(), x.getUTCDate());
  const today = utcMidnight(now);
  const target = utcMidnight(d);
  const ONE_DAY = 24 * 60 * 60 * 1000;
  const dayDiff = Math.round((today - target) / ONE_DAY);

  if (dayDiff === 0) return { key: "today", label: "Today", rank: 1_000_000 };
  if (dayDiff === 1)
    return { key: "yesterday", label: "Yesterday", rank: 999_999 };

  // "Week" = the UTC calendar week containing `now` (Sunday → Saturday).
  const nowDow = new Date(today).getUTCDay(); // 0 = Sun
  const startOfThisWeek = today - nowDow * ONE_DAY;
  const startOfLastWeek = startOfThisWeek - 7 * ONE_DAY;

  if (target >= startOfThisWeek && dayDiff >= 2) {
    return {
      key: "earlier-this-week",
      label: "Earlier this week",
      rank: 999_998,
    };
  }
  if (target >= startOfLastWeek) {
    return { key: "last-week", label: "Last week", rank: 999_997 };
  }

  const startOfThisMonth = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1);
  if (target >= startOfThisMonth) {
    return {
      key: "earlier-this-month",
      label: "Earlier this month",
      rank: 999_996,
    };
  }

  // Older: bucket per calendar month. Rank by year*12 + month so months
  // sort newest-first within "older" while staying below the relative
  // buckets above. We label using a UTC-anchored date so the label name
  // matches the bucket math.
  const y = d.getUTCFullYear();
  const m = d.getUTCMonth(); // 0–11
  const key = `${y}-${String(m + 1).padStart(2, "0")}`;
  const label = new Date(Date.UTC(y, m, 1)).toLocaleDateString(undefined, {
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
  return { key, label, rank: y * 12 + m };
}

export type App = {
  id: string;
  companyName: string;
  jobRole: string | null;
  jobUrl: string | null;
  location: string | null;
  interviewStatus: string | null;
  status: string;
  appliedDate: string;
  resumeId: string | null;
  resumeLabel: string | null;
  companyCareerPage: string | null;
  decisionDate: string | null;
  decisionTime: string | null;
  notes: string | null;
  hrName: string | null;
  hrLinkedin: string | null;
  hrEmail: string | null;
  referral: string | null;
  referralLinkedin: string | null;
  jobDescription: string | null;
  linkedLeads: LinkedLead[];
  reachOuts: AppReachOut[];
};

export type LinkedLead = {
  id: string;
  name: string;
  email: string | null;
  linkedinUrl: string | null;
  linkedinProfile: string | null;
  currentCompany: string | null;
  role: string | null;
  linkRole: string | null;
};

export type AppReachOut = {
  id: string;
  subject: string;
  status: string;
  sentAt: string | null;
  recipientName: string;
  recipientEmail: string;
  createdAt: string;
};

export default function ApplicationsTable({
  apps,
  resumes,
  total,
  query: serverQuery,
  status,
}: {
  apps: App[];
  resumes: { id: string; label: string }[];
  total: number;
  // The query the server actually filtered by (mirrors the `q` URL param).
  query: string;
  // Active status tab, preserved when we rewrite the URL on search.
  status: string;
}) {
  const router = useRouter();
  // Local input state for a responsive field; the URL (and thus the DB
  // query) is updated on a debounce so search hits *all* applications,
  // not just the loaded browse window.
  const [query, setQuery] = useState(serverQuery);

  // Keep the input in sync if the server query changes underneath us
  // (e.g. back/forward navigation).
  useEffect(() => {
    setQuery(serverQuery);
  }, [serverQuery]);

  // Push the debounced query into the URL. Searching server-side means we
  // navigate rather than filter in memory; status is preserved, and `all`
  // is dropped because a query returns all its own matches.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const next = query.trim();
    if (next === serverQuery) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      const params = new URLSearchParams();
      if (status !== "all") params.set("status", status);
      if (next) params.set("q", next);
      const qs = params.toString();
      router.replace(qs ? `/applications?${qs}` : "/applications");
    }, 300);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, serverQuery, status, router]);

  // Server already filtered; render what we were given.
  const filtered = apps;

  // Stable slot numbers based on the original (pre-group) server order, so
  // the "Sl" column doesn't reshuffle when a row jumps date groups.
  const slNoById = useMemo(() => {
    const m = new Map<string, number>();
    apps.forEach((a, idx) => m.set(a.id, apps.length - idx));
    return m;
  }, [apps]);

  // Group filtered rows by date bucket. Each group's rows are already in
  // appliedDate-desc order because the server sorts that way and `filter`
  // preserves order. Buckets themselves sort by rank (newest first).
  const groups = useMemo(() => {
    const byKey = new Map<
      string,
      { key: string; label: string; rank: number; apps: App[] }
    >();
    const now = new Date();
    for (const a of filtered) {
      const b = bucketFor(a.appliedDate, now);
      const existing = byKey.get(b.key);
      if (existing) {
        existing.apps.push(a);
      } else {
        byKey.set(b.key, { ...b, apps: [a] });
      }
    }
    return Array.from(byKey.values()).sort((x, y) => y.rank - x.rank);
  }, [filtered]);

  return (
    <>
      {/* ── Search bar ── */}
      <div className="search-bar relative flex items-center gap-2 rounded-xl border border-base-300/60 bg-base-200/40 px-3.5 h-10 transition-colors focus-within:border-primary/60 focus-within:bg-base-200/70 hover:border-base-300">
        <svg
          className="pointer-events-none h-4 w-4 opacity-50 shrink-0"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M21 21l-4.35-4.35M11 19a8 8 0 100-16 8 8 0 000 16z"
          />
        </svg>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by company, role, location, status…"
          className="flex-1 bg-transparent border-0 outline-none focus:outline-none focus:ring-0 focus:border-0 shadow-none text-sm placeholder:opacity-40"
          id="applications-search"
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            className="text-xs opacity-50 hover:opacity-100 shrink-0 transition-opacity"
            aria-label="Clear search"
          >
            ✕
          </button>
        )}
      </div>

      {/* ── Result count when filtering ── */}
      {query.trim() && (
        <p className="text-xs opacity-50 -mt-3">
          {filtered.length} of {total} applications match &ldquo;
          <span className="font-medium">{query}</span>&rdquo;
        </p>
      )}

      {/* ── Table ── */}
      {filtered.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-3">🔍</div>
          <p className="text-sm opacity-60">
            {query.trim()
              ? `No applications matching "${query}"`
              : "No applications yet — save one from the extension popup\u2019s Track tab."}
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr className="border-b border-base-300/40">
                <th className="opacity-50">Sl</th>
                <th>Company</th>
                <th>Role</th>
                <th>Location</th>
                <th>Interview</th>
                <th>Status</th>
                <th>Applied</th>
                <th>Resume</th>
                <th>Decision</th>
                <th>Time</th>
                <th className="text-right pr-4">Reach out</th>
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.key}>
                <tr className="bg-base-200/40">
                  <td
                    colSpan={COLUMN_COUNT}
                    className="px-4 py-2 text-xs font-semibold uppercase tracking-wide opacity-60 border-y border-base-300/40"
                  >
                    {g.label}
                    <span className="ml-2 font-mono opacity-50 normal-case tracking-normal">
                      {g.apps.length}
                    </span>
                  </td>
                </tr>
                {g.apps.map((a) => (
                  <ApplicationsRow
                    key={a.id}
                    slNo={slNoById.get(a.id) ?? 0}
                    resumes={resumes}
                    app={a}
                  />
                ))}
              </tbody>
            ))}
          </table>
        </div>
      )}
    </>
  );
}
