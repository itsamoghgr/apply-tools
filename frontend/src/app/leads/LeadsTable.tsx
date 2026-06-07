"use client";

import { useState, useMemo } from "react";
import { Plus, Search } from "lucide-react";
import LeadsRow from "./LeadsRow";
import AddLeadForm from "./AddLeadForm";

export type LeadReachOut = {
  id: string;
  status: string;
  subject: string;
  sentAt: string | null;
  createdAt: string;
};

export type Lead = {
  id: string;
  name: string;
  email: string | null;
  linkedinUrl: string | null;
  linkedinProfile: string | null;
  currentCompany: string | null;
  role: string | null;
  replied: boolean;
  repliedAt: string | null;
  notes: string | null;
  createdAt: string;
  updatedAt: string;
  reachOuts: LeadReachOut[];
};

export default function LeadsTable({
  leads,
  total,
}: {
  leads: Lead[];
  total: number;
}) {
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);

  const filtered = useMemo(() => {
    if (!query.trim()) return leads;
    const q = query.toLowerCase();
    return leads.filter(
      (l) =>
        l.name.toLowerCase().includes(q) ||
        (l.email ?? "").toLowerCase().includes(q) ||
        (l.currentCompany ?? "").toLowerCase().includes(q) ||
        (l.role ?? "").toLowerCase().includes(q) ||
        (l.notes ?? "").toLowerCase().includes(q)
    );
  }, [leads, query]);

  return (
    <>
      <div className="flex items-center gap-3 flex-wrap">
        <div className="search-bar relative flex items-center gap-2 rounded-xl border border-base-300/60 bg-base-200/40 px-3.5 h-10 transition-colors focus-within:border-primary/60 focus-within:bg-base-200/70 hover:border-base-300 flex-1 min-w-[260px]">
          <Search className="pointer-events-none h-4 w-4 opacity-50 shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name, email, company, role…"
            className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:opacity-40"
            id="leads-search"
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
        <button
          type="button"
          onClick={() => setAdding(true)}
          className="btn btn-primary btn-sm gap-1.5 shrink-0"
        >
          <Plus className="h-4 w-4" />
          Add lead
        </button>
      </div>

      {query.trim() && (
        <p className="text-xs opacity-50 -mt-3">
          {filtered.length} of {total} leads match &ldquo;
          <span className="font-medium">{query}</span>&rdquo;
        </p>
      )}

      {adding && <AddLeadForm onClose={() => setAdding(false)} />}

      {filtered.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-3">👥</div>
          <p className="text-sm opacity-60">
            {query.trim()
              ? `No leads matching "${query}"`
              : "No leads yet — click \u201cAdd lead\u201d to track your first contact."}
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-x-auto">
          <table className="table table-sm">
            <thead>
              <tr className="border-b border-base-300/40">
                <th className="opacity-50">Sl</th>
                <th>Name</th>
                <th>Email</th>
                <th>Company</th>
                <th>Role</th>
                <th>LinkedIn</th>
                <th className="text-center">Reach‑outs</th>
                <th className="text-center">Replied</th>
                <th>Updated</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((l, i) => (
                <LeadsRow
                  key={l.id}
                  slNo={total - leads.indexOf(l)}
                  lead={l}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
