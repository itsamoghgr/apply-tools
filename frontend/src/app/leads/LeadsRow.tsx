"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Pencil, ExternalLink, Trash2, Mail, Send } from "lucide-react";
import type { Lead } from "./LeadsTable";
import { relativeTime } from "@/lib/time";

const FIELD_LABELS: Partial<Record<keyof Lead, string>> = {
  name: "Name",
  email: "Email",
  linkedinUrl: "LinkedIn URL",
  linkedinProfile: "LinkedIn profile",
  currentCompany: "Company",
  role: "Role",
  replied: "Replied",
  repliedAt: "Replied at",
  notes: "Notes",
};

function fieldLabel(field: keyof Lead): string {
  return FIELD_LABELS[field] ?? String(field);
}

const STATUS_BADGE: Record<string, string> = {
  draft: "badge-ghost",
  sent: "badge-success",
  failed: "badge-error",
};

export default function LeadsRow({ slNo, lead }: { slNo: number; lead: Lead }) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [local, setLocal] = useState(lead);
  const [open, setOpen] = useState(false);

  async function patch(
    field: keyof Lead,
    value: string | boolean | null
  ): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    try {
      const res = await fetch(`/api/proxy/leads/${lead.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      setLocal((prev) => ({ ...prev, [field]: value as never }));
      toast.success(
        value === null || value === ""
          ? `${fieldLabel(field)} cleared`
          : `${fieldLabel(field)} updated`
      );
      startTransition(() => router.refresh());
      return true;
    } catch (e) {
      toast.error(`${fieldLabel(field)} save failed: ${(e as Error).message}`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function patchMany(updates: Partial<Lead>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    try {
      const res = await fetch(`/api/proxy/leads/${lead.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      setLocal((prev) => ({ ...prev, ...updates }));
      const keys = Object.keys(updates) as (keyof Lead)[];
      toast.success(
        keys.length === 1
          ? `${fieldLabel(keys[0])} updated`
          : `Saved ${keys.length} changes`
      );
      startTransition(() => router.refresh());
      return true;
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    setBusy(true);
    try {
      const res = await fetch(`/api/proxy/leads/${lead.id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success(`Deleted ${lead.name}`);
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  const cellInput =
    "w-full bg-transparent outline-none focus:bg-base-200 rounded px-1 py-0.5 transition-colors";

  const sentCount = local.reachOuts.filter((r) => r.status === "sent").length;
  const totalReachOuts = local.reachOuts.length;

  return (
    <>
      <tr
        className={`align-middle whitespace-nowrap cursor-pointer transition-colors ${
          open ? "bg-base-200/60" : "hover:bg-base-200/40"
        }`}
        onClick={(e) => {
          const t = e.target as HTMLElement;
          if (
            t.closest("input, select, textarea, button, a, [contenteditable]")
          ) {
            return;
          }
          setOpen((o) => !o);
        }}
        aria-expanded={open}
      >
        <td className="opacity-40 text-xs tabular-nums">{slNo}</td>
        <td className="font-medium">
          <EditableText
            value={local.name}
            onSave={(v) => patch("name", v || lead.name)}
            className={cellInput}
          />
        </td>
        <td>
          <EmailCell
            value={local.email ?? ""}
            onSave={(v) => patch("email", v || null)}
            className={cellInput}
          />
        </td>
        <td>
          <EditableText
            value={local.currentCompany ?? ""}
            onSave={(v) => patch("currentCompany", v || null)}
            className={cellInput}
            placeholder="Add company"
          />
        </td>
        <td>
          <EditableText
            value={local.role ?? ""}
            onSave={(v) => patch("role", v || null)}
            className={cellInput}
            placeholder="Add role"
          />
        </td>
        <td>
          <LinkedinCell
            value={local.linkedinUrl ?? ""}
            onSave={(v) => patch("linkedinUrl", v || null)}
            className={cellInput}
          />
        </td>
        <td className="text-center">
          {totalReachOuts === 0 ? (
            <span className="text-xs opacity-30">—</span>
          ) : (
            <span
              className="badge badge-ghost badge-sm font-mono tabular-nums"
              title={`${sentCount} sent · ${totalReachOuts - sentCount} draft/failed`}
            >
              {sentCount}/{totalReachOuts}
            </span>
          )}
        </td>
        <td className="text-center">
          <input
            type="checkbox"
            checked={local.replied}
            onChange={(e) => patch("replied", e.target.checked)}
            disabled={busy}
            className="checkbox checkbox-sm checkbox-success"
            aria-label="Mark replied"
          />
        </td>
        <td>
          <span
            className="opacity-50 text-xs tabular-nums"
            title={new Date(local.updatedAt).toLocaleString()}
          >
            {relativeTime(new Date(local.updatedAt))}
          </span>
        </td>
      </tr>
      {open && (
        <tr className="bg-base-200/40">
          <td colSpan={9} className="p-0 border-t border-base-300/40">
            <DetailsPanel
              lead={local}
              busy={busy}
              onSaveMany={patchMany}
              onDelete={onDelete}
            />
          </td>
        </tr>
      )}
    </>
  );
}

// ─── Details panel ───────────────────────────────────────────────────────────

const DETAIL_FIELDS = [
  "linkedinUrl",
  "linkedinProfile",
  "notes",
] as const;
type DetailField = (typeof DETAIL_FIELDS)[number];
type Draft = Record<DetailField, string>;

function leadToDraft(lead: Lead): Draft {
  return {
    linkedinUrl: lead.linkedinUrl ?? "",
    linkedinProfile: lead.linkedinProfile ?? "",
    notes: lead.notes ?? "",
  };
}

function diffDraft(prev: Draft, next: Draft): Partial<Lead> {
  const out: Partial<Lead> = {};
  for (const k of DETAIL_FIELDS) {
    if (prev[k] !== next[k]) {
      out[k] = (next[k] === "" ? null : next[k]) as never;
    }
  }
  return out;
}

function DetailsPanel({
  lead,
  busy,
  onSaveMany,
  onDelete,
}: {
  lead: Lead;
  busy: boolean;
  onSaveMany: (updates: Partial<Lead>) => Promise<boolean>;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => leadToDraft(lead));

  if (!editing) {
    const fresh = leadToDraft(lead);
    let same = true;
    for (const k of DETAIL_FIELDS) if (fresh[k] !== draft[k]) { same = false; break; }
    if (!same) setDraft(fresh);
  }

  const setField = (k: DetailField, v: string) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const onEdit = () => setEditing(true);
  const onCancel = () => {
    setDraft(leadToDraft(lead));
    setEditing(false);
  };
  const onSave = async () => {
    const updates = diffDraft(leadToDraft(lead), draft);
    if (Object.keys(updates).length === 0) {
      setEditing(false);
      return;
    }
    const ok = await onSaveMany(updates);
    if (ok) setEditing(false);
  };

  return (
    <div className="px-8 py-6 animate-fade-in">
      <div className="max-w-5xl">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-base font-semibold tracking-tight">Lead details</h2>
          {editing ? (
            <div className="flex items-center gap-1">
              <button
                onClick={onCancel}
                disabled={busy}
                className="inline-flex items-center text-xs font-medium opacity-70 hover:opacity-100 hover:bg-base-300/40 rounded-md px-2.5 py-1.5 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Cancel
              </button>
              <button
                onClick={onSave}
                disabled={busy}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary-content bg-primary hover:bg-primary/90 rounded-md px-3 py-1.5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? "Saving…" : "Save changes"}
              </button>
            </div>
          ) : (
            <button
              onClick={onEdit}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-primary/80 hover:text-primary hover:bg-primary/10 rounded-md px-2.5 py-1.5 transition-colors"
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit profile + notes
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-6">
          <Section label="LinkedIn URL" full>
            {editing ? (
              <input
                type="url"
                value={draft.linkedinUrl}
                onChange={(e) => setField("linkedinUrl", e.target.value)}
                placeholder="https://www.linkedin.com/in/..."
                className="input input-bordered input-sm w-full"
              />
            ) : (
              <ReadUrl value={draft.linkedinUrl} />
            )}
          </Section>

          <Section label="LinkedIn profile (pasted text)" full>
            {editing ? (
              <textarea
                value={draft.linkedinProfile}
                onChange={(e) => setField("linkedinProfile", e.target.value)}
                rows={10}
                placeholder="Paste the profile text from the LinkedIn PDF here. The Reach Out composer reads this when generating personalized emails."
                className="textarea textarea-bordered textarea-sm w-full font-mono text-xs leading-relaxed"
              />
            ) : draft.linkedinProfile ? (
              <pre className="text-xs whitespace-pre-wrap leading-relaxed text-base-content/80 max-h-[260px] overflow-y-auto bg-base-200/40 rounded-md p-3 border border-base-300/40 font-mono">
                {draft.linkedinProfile}
              </pre>
            ) : (
              <span className="text-sm opacity-30 italic">
                No profile text pasted yet
              </span>
            )}
          </Section>

          <Section label="Notes" full>
            {editing ? (
              <textarea
                value={draft.notes}
                onChange={(e) => setField("notes", e.target.value)}
                rows={3}
                placeholder="Anything to remember about this lead — how you found them, what to mention, follow-up date, etc."
                className="textarea textarea-bordered textarea-sm w-full"
              />
            ) : draft.notes ? (
              <p className="text-sm whitespace-pre-wrap leading-relaxed text-base-content">
                {draft.notes}
              </p>
            ) : (
              <span className="text-sm opacity-30 italic">No notes</span>
            )}
          </Section>

          <Section label="Reach-out history" full>
            {lead.reachOuts.length === 0 ? (
              <p className="text-sm opacity-40 italic">
                No reach-outs yet. Draft one from the{" "}
                <a href="/reach-out" className="link link-primary">
                  Reach Out
                </a>{" "}
                page; we&apos;ll auto-link it here by email.
              </p>
            ) : (
              <ul className="space-y-1.5">
                {lead.reachOuts.map((r) => (
                  <li
                    key={r.id}
                    className="flex items-center gap-3 px-3 py-2 rounded-md bg-base-200/40 border border-base-300/40"
                  >
                    <span
                      className={`badge badge-sm ${STATUS_BADGE[r.status] ?? "badge-ghost"} shrink-0`}
                    >
                      {r.status}
                    </span>
                    <Send className="h-3.5 w-3.5 opacity-40 shrink-0" />
                    <span className="text-sm truncate flex-1">
                      {r.subject || <span className="opacity-40 italic">(no subject)</span>}
                    </span>
                    <span
                      className="text-xs opacity-50 tabular-nums whitespace-nowrap"
                      title={new Date(r.sentAt ?? r.createdAt).toLocaleString()}
                    >
                      {relativeTime(new Date(r.sentAt ?? r.createdAt))}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {lead.replied && lead.repliedAt && (
            <Section label="Replied at" full>
              <span className="text-sm">
                {new Date(lead.repliedAt).toLocaleString()}
              </span>
            </Section>
          )}
        </div>

        <div className="flex justify-end items-center pt-5 mt-6 border-t border-base-300/40">
          {confirmingDelete ? (
            <div className="flex items-center gap-3">
              <span className="text-xs opacity-70">
                Delete <span className="font-medium">{lead.name}</span>? This
                can&apos;t be undone. Linked reach-outs will be kept but
                unlinked.
              </span>
              <button
                onClick={() => setConfirmingDelete(false)}
                disabled={busy}
                className="btn btn-ghost btn-xs"
              >
                Cancel
              </button>
              <button
                onClick={onDelete}
                disabled={busy}
                className="btn btn-error btn-xs"
              >
                {busy ? "Deleting…" : "Confirm delete"}
              </button>
            </div>
          ) : (
            <button
              onClick={() => setConfirmingDelete(true)}
              disabled={busy || editing}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-error/80 hover:text-error hover:bg-error/10 rounded-md px-2.5 py-1.5 transition-colors disabled:opacity-40 disabled:hover:bg-transparent disabled:cursor-not-allowed"
            >
              <Trash2 className="h-3.5 w-3.5" />
              Delete lead
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Section({
  label,
  children,
  full,
}: {
  label: string;
  children: React.ReactNode;
  full?: boolean;
}) {
  return (
    <div className={full ? "md:col-span-2" : ""}>
      <div className="flex items-center gap-2 mb-3">
        <span className="inline-block h-3 w-0.5 rounded-full bg-primary/70" />
        <h3 className="text-sm font-semibold tracking-wide text-base-content">
          {label}
        </h3>
      </div>
      <div className="pl-3.5">{children}</div>
    </div>
  );
}

// ─── Cell primitives ─────────────────────────────────────────────────────────

function ReadUrl({ value }: { value: string }) {
  if (!value) return <span className="text-xs opacity-30 italic">—</span>;
  let pretty = value;
  try {
    const u = new URL(value);
    pretty = u.host + (u.pathname && u.pathname !== "/" ? u.pathname : "");
  } catch {
    /* keep raw */
  }
  if (pretty.length > 64) pretty = pretty.slice(0, 63) + "…";
  return (
    <a
      href={value}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 link link-primary text-sm min-w-0"
      title={value}
    >
      <span className="truncate">{pretty}</span>
      <ExternalLink className="h-3 w-3 shrink-0 opacity-60" />
    </a>
  );
}

function EditableText({
  value,
  onSave,
  className,
  placeholder,
}: {
  value: string;
  onSave: (v: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);

  const commit = () => {
    setEditing(false);
    if (v !== value) onSave(v);
  };

  if (!editing) {
    return (
      <span
        onClick={() => {
          setV(value);
          setEditing(true);
        }}
        className={`block cursor-text rounded px-1 py-0.5 hover:bg-base-300/40 transition-colors ${
          value ? "" : "opacity-40 italic text-xs"
        }`}
        title="Click to edit"
      >
        {value || placeholder || "Add"}
      </span>
    );
  }
  return (
    <input
      type="text"
      value={v}
      autoFocus
      placeholder={placeholder}
      onChange={(e) => setV(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === "Enter") (e.target as HTMLInputElement).blur();
        if (e.key === "Escape") {
          setV(value);
          setEditing(false);
        }
      }}
      className={className}
    />
  );
}

function EmailCell({
  value,
  onSave,
  className,
}: {
  value: string;
  onSave: (v: string) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);

  const commit = () => {
    setEditing(false);
    if (v !== value) onSave(v);
  };

  if (editing) {
    return (
      <input
        type="email"
        value={v}
        autoFocus
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setV(value);
            setEditing(false);
          }
        }}
        className={className}
      />
    );
  }

  if (!value) {
    return (
      <span
        onClick={() => {
          setV("");
          setEditing(true);
        }}
        className="block cursor-text rounded px-1 py-0.5 hover:bg-base-300/40 transition-colors opacity-40 italic text-xs"
        title="Click to edit"
      >
        Add email
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5 group min-w-0">
      <a
        href={`mailto:${value}`}
        onClick={(e) => e.stopPropagation()}
        className="opacity-60 hover:opacity-100 hover:text-primary transition-opacity shrink-0"
        title={`Email ${value}`}
      >
        <Mail className="h-3 w-3" />
      </a>
      <span
        onClick={() => {
          setV(value);
          setEditing(true);
        }}
        className="cursor-text rounded px-1 py-0.5 hover:bg-base-300/40 transition-colors text-sm truncate max-w-[220px]"
        title="Click to edit"
      >
        {value}
      </span>
    </span>
  );
}

function LinkedinCell({
  value,
  onSave,
  className,
}: {
  value: string;
  onSave: (v: string) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);

  const commit = () => {
    setEditing(false);
    if (v !== value) onSave(v);
  };

  if (editing) {
    return (
      <input
        type="url"
        value={v}
        autoFocus
        placeholder="https://www.linkedin.com/in/..."
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setV(value);
            setEditing(false);
          }
        }}
        className={className}
      />
    );
  }

  if (!value) {
    return (
      <span
        onClick={() => {
          setV("");
          setEditing(true);
        }}
        className="block cursor-text rounded px-1 py-0.5 hover:bg-base-300/40 transition-colors opacity-40 italic text-xs"
        title="Click to edit"
      >
        Add URL
      </span>
    );
  }

  return (
    <a
      href={value}
      target="_blank"
      rel="noopener noreferrer"
      className="inline-flex items-center gap-1 link link-primary text-sm"
      onDoubleClick={(e) => {
        e.preventDefault();
        setV(value);
        setEditing(true);
      }}
      onClick={(e) => e.stopPropagation()}
      title="Click to open · double-click to edit"
    >
      <span>Profile</span>
      <ExternalLink className="h-3 w-3 opacity-40" />
    </a>
  );
}
