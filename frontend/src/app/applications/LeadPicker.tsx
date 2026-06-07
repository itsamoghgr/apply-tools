"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { Search, Plus, X } from "lucide-react";

export type PickableLead = {
  id: string;
  name: string;
  email: string | null;
  currentCompany: string | null;
  role: string | null;
};

type Props = {
  title?: string;
  excludeLeadIds?: string[];
  onPick: (leadId: string, role: string | null) => void | Promise<void>;
  onClose: () => void;
  showRoleField?: boolean;
};

const ROLE_SUGGESTIONS = ["HR", "Recruiter", "Referral", "Hiring Manager"];

export default function LeadPicker({
  title = "Link a lead",
  excludeLeadIds = [],
  onPick,
  onClose,
  showRoleField = true,
}: Props) {
  const [leads, setLeads] = useState<PickableLead[] | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newLinkedin, setNewLinkedin] = useState("");
  const [role, setRole] = useState("");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/proxy/leads")
      .then((r) => r.json())
      .then((j) => {
        if (cancelled) return;
        const list: PickableLead[] = (j.leads ?? []).map(
          (l: Record<string, unknown>) => ({
            id: String(l.id),
            name: String(l.name ?? ""),
            email: (l.email as string | null) ?? null,
            currentCompany: (l.currentCompany as string | null) ?? null,
            role: (l.role as string | null) ?? null,
          })
        );
        setLeads(list);
      })
      .catch(() => {
        if (!cancelled) toast.error("Failed to load leads");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const visible = useMemo(() => {
    const exclude = new Set(excludeLeadIds);
    const base = (leads ?? []).filter((l) => !exclude.has(l.id));
    if (!query.trim()) return base;
    const q = query.toLowerCase();
    return base.filter(
      (l) =>
        l.name.toLowerCase().includes(q) ||
        (l.email ?? "").toLowerCase().includes(q) ||
        (l.currentCompany ?? "").toLowerCase().includes(q) ||
        (l.role ?? "").toLowerCase().includes(q)
    );
  }, [leads, query, excludeLeadIds]);

  async function pick(leadId: string) {
    if (busy) return;
    setBusy(true);
    try {
      await onPick(leadId, role.trim() || null);
    } finally {
      setBusy(false);
    }
  }

  async function createAndPick(e: React.FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!newName.trim()) {
      toast.error("Name is required");
      return;
    }
    setBusy(true);
    try {
      const res = await fetch("/api/proxy/leads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: newName.trim(),
          email: newEmail.trim() || null,
          linkedinUrl: newLinkedin.trim() || null,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const created = (await res.json()) as { id: string };
      await onPick(created.id, role.trim() || null);
    } catch (err) {
      toast.error(`Create failed: ${(err as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center p-4 bg-black/40 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="mt-20 w-full max-w-md rounded-xl bg-base-100 shadow-2xl border border-base-300/60 overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-base-300/40">
          <h2 className="text-sm font-semibold tracking-tight">{title}</h2>
          <button
            onClick={onClose}
            disabled={busy}
            className="text-xs opacity-60 hover:opacity-100 transition-opacity disabled:opacity-30"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {creating ? (
          <form onSubmit={createAndPick} className="px-4 py-4 space-y-3">
            <div>
              <label className="text-[11px] uppercase tracking-wider opacity-60 block mb-1">
                Name *
              </label>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                autoFocus
                className="input input-bordered input-sm w-full"
                placeholder="Jane Doe"
              />
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider opacity-60 block mb-1">
                Email
              </label>
              <input
                type="email"
                value={newEmail}
                onChange={(e) => setNewEmail(e.target.value)}
                className="input input-bordered input-sm w-full"
                placeholder="jane@acme.com"
              />
            </div>
            <div>
              <label className="text-[11px] uppercase tracking-wider opacity-60 block mb-1">
                LinkedIn URL
              </label>
              <input
                type="url"
                value={newLinkedin}
                onChange={(e) => setNewLinkedin(e.target.value)}
                className="input input-bordered input-sm w-full"
                placeholder="https://linkedin.com/in/…"
              />
            </div>
            {showRoleField && (
              <RoleField role={role} setRole={setRole} />
            )}
            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={() => setCreating(false)}
                disabled={busy}
                className="btn btn-ghost btn-sm"
              >
                Back
              </button>
              <button
                type="submit"
                disabled={busy}
                className="btn btn-primary btn-sm"
              >
                {busy ? "Creating…" : "Create & link"}
              </button>
            </div>
          </form>
        ) : (
          <>
            <div className="px-4 py-3 border-b border-base-300/40">
              <div className="search-bar relative flex items-center gap-2 rounded-lg border border-base-300/60 bg-base-200/40 px-3 h-9 focus-within:border-primary/60">
                <Search className="pointer-events-none h-4 w-4 opacity-50 shrink-0" />
                <input
                  type="text"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search leads…"
                  autoFocus
                  className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:opacity-40"
                />
              </div>
              {showRoleField && (
                <div className="mt-3">
                  <RoleField role={role} setRole={setRole} />
                </div>
              )}
            </div>

            <div className="max-h-72 overflow-y-auto">
              {leads === null ? (
                <div className="p-6 text-center text-xs opacity-50">
                  Loading…
                </div>
              ) : visible.length === 0 ? (
                <div className="p-6 text-center text-xs opacity-50">
                  {query
                    ? `No leads matching "${query}"`
                    : "No leads available."}
                </div>
              ) : (
                <ul className="divide-y divide-base-300/40">
                  {visible.map((l) => (
                    <li key={l.id}>
                      <button
                        type="button"
                        onClick={() => pick(l.id)}
                        disabled={busy}
                        className="w-full text-left px-4 py-2.5 hover:bg-base-200/60 transition-colors disabled:opacity-50"
                      >
                        <div className="text-sm font-medium">{l.name}</div>
                        <div className="text-xs opacity-60 truncate">
                          {[l.email, l.role, l.currentCompany]
                            .filter(Boolean)
                            .join(" · ") || "—"}
                        </div>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="flex justify-end px-4 py-3 border-t border-base-300/40">
              <button
                type="button"
                onClick={() => setCreating(true)}
                disabled={busy}
                className="btn btn-ghost btn-sm gap-1.5"
              >
                <Plus className="h-3.5 w-3.5" />
                New lead
              </button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body
  );
}

function RoleField({
  role,
  setRole,
}: {
  role: string;
  setRole: (v: string) => void;
}) {
  return (
    <div>
      <label className="text-[11px] uppercase tracking-wider opacity-60 block mb-1">
        Role tag (optional)
      </label>
      <input
        type="text"
        value={role}
        onChange={(e) => setRole(e.target.value)}
        list="lead-role-suggestions"
        className="input input-bordered input-sm w-full"
        placeholder="HR, Recruiter, Referral…"
      />
      <datalist id="lead-role-suggestions">
        {ROLE_SUGGESTIONS.map((r) => (
          <option key={r} value={r} />
        ))}
      </datalist>
    </div>
  );
}
