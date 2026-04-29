"use client";

import { useState, useMemo } from "react";
import ApplicationsRow from "./ApplicationsRow";

type App = {
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
};

export default function ApplicationsTable({
  apps,
  resumes,
  total,
}: {
  apps: App[];
  resumes: { id: string; label: string }[];
  total: number;
}) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    if (!query.trim()) return apps;
    const q = query.toLowerCase();
    return apps.filter(
      (a) =>
        a.companyName.toLowerCase().includes(q) ||
        (a.jobRole ?? "").toLowerCase().includes(q) ||
        (a.location ?? "").toLowerCase().includes(q) ||
        (a.status ?? "").toLowerCase().includes(q) ||
        (a.interviewStatus ?? "").toLowerCase().includes(q) ||
        (a.notes ?? "").toLowerCase().includes(q) ||
        (a.hrName ?? "").toLowerCase().includes(q) ||
        (a.referral ?? "").toLowerCase().includes(q)
    );
  }, [apps, query]);

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
          className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:opacity-40"
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
              </tr>
            </thead>
            <tbody>
              {filtered.map((a, i) => (
                <ApplicationsRow
                  key={a.id}
                  slNo={total - apps.indexOf(a)}
                  resumes={resumes}
                  app={a}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
