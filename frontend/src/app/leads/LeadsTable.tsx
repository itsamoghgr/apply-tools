"use client";

import { useState, useMemo } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Plus, Search, MailSearch, Loader2 } from "lucide-react";
import LeadsRow from "./LeadsRow";
import AddLeadForm from "./AddLeadForm";
import FindEmailsProgress, { type FindRow } from "./FindEmailsProgress";

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
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [finding, setFinding] = useState(false);

  // Live progress for the bulk Find-emails run (FindRow type from the panel).
  const [progress, setProgress] = useState<{
    done: number;
    total: number;
    rows: FindRow[];
  } | null>(null);

  // Leads with no email but something to work from (LinkedIn or a company).
  const missingEmail = useMemo(
    () => leads.filter((l) => !l.email && (l.currentCompany || l.linkedinUrl)),
    [leads]
  );

  // Bulk "Find emails": run the agentic contact-finder for each lead missing an
  // email, patch any address found, and stream per-lead progress to the UI.
  async function findEmails() {
    if (finding || missingEmail.length === 0) return;
    setFinding(true);
    const rows: FindRow[] = missingEmail.map((l) => ({
      name: l.name,
      status: "pending",
    }));
    setProgress({ done: 0, total: missingEmail.length, rows });
    let found = 0;

    const update = (i: number, patch: Partial<FindRow>) =>
      setProgress((p) =>
        p
          ? {
              ...p,
              rows: p.rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)),
            }
          : p
      );

    try {
      for (let i = 0; i < missingEmail.length; i++) {
        const l = missingEmail[i];
        update(i, { status: "searching" });
        try {
          const res = await fetch(`/api/agent/api/v1/verify/email`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({
              company: l.currentCompany,
              founder_name: l.name,
            }),
          });
          const v = res.ok ? await res.json() : {};
          if (v.email) {
            await fetch(`/api/proxy/leads/${l.id}`, {
              method: "PATCH",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ email: v.email }),
            });
            found += 1;
            update(i, { status: "found", email: v.email, method: v.method });
          } else {
            update(i, { status: "miss" });
          }
        } catch {
          update(i, { status: "miss" });
        }
        setProgress((p) => (p ? { ...p, done: p.done + 1 } : p));
      }
      if (found > 0) {
        toast.success(`Found ${found} email${found === 1 ? "" : "s"}.`);
        router.refresh();
      } else {
        toast(`No new emails found for ${missingEmail.length} lead(s).`);
      }
    } finally {
      setFinding(false);
      // Leave the completed panel up briefly so the user sees the summary,
      // then auto-dismiss.
      setTimeout(() => setProgress(null), 6000);
    }
  }

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
        {missingEmail.length > 0 && (
          <button
            type="button"
            onClick={findEmails}
            disabled={finding}
            className="btn btn-outline btn-sm gap-1.5 shrink-0"
            title={`Find emails for ${missingEmail.length} lead(s) missing one`}
          >
            {finding ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <MailSearch className="h-4 w-4" />
            )}
            {finding && progress
              ? `Finding ${progress.done}/${progress.total}…`
              : `Find emails (${missingEmail.length})`}
          </button>
        )}
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

      {/* Live Find-emails progress: the contact agent works one lead at a time. */}
      {progress && (
        <FindEmailsProgress progress={progress} finding={finding} />
      )}

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
                <th></th>
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
