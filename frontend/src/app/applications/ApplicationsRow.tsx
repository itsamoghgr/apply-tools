"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Pencil, ExternalLink, Trash2 } from "lucide-react";

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

const STATUS_OPTIONS = [
  "Applied",
  "In-Progress",
  "Offer",
  "Rejected",
  "Withdrawn",
  "Ghosted",
];

const INTERVIEW_OPTIONS = ["", "Assessment", "Interviewing", "Offer", "Rejected"];

const STATUS_BADGE: Record<string, string> = {
  Applied: "badge-ghost",
  "In-Progress": "badge-warning",
  Offer: "badge-success",
  Rejected: "badge-error",
  Withdrawn: "badge-neutral",
  Ghosted: "badge-neutral badge-outline",
};

const INTERVIEW_BADGE: Record<string, string> = {
  Assessment: "badge-warning",
  Interviewing: "badge-warning badge-outline",
  Offer: "badge-success",
  Rejected: "badge-error",
};

const FIELD_LABELS: Partial<Record<keyof App, string>> = {
  companyName: "Company",
  jobRole: "Role",
  jobUrl: "Job URL",
  location: "Location",
  interviewStatus: "Interview status",
  status: "Status",
  appliedDate: "Applied date",
  resumeId: "Resume",
  decisionDate: "Decision date",
  decisionTime: "Decision time",
  companyCareerPage: "Career page",
  hrName: "HR name",
  hrEmail: "HR email",
  hrLinkedin: "HR LinkedIn",
  referral: "Referral",
  referralLinkedin: "Referral LinkedIn",
  notes: "Notes",
};

function fieldLabel(field: keyof App): string {
  return FIELD_LABELS[field] ?? String(field);
}

function fmtDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(d.getUTCDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

export default function ApplicationsRow({
  slNo,
  app,
  resumes,
}: {
  slNo: number;
  app: App;
  resumes: { id: string; label: string }[];
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [local, setLocal] = useState(app);
  const [open, setOpen] = useState(false);

  async function patch(field: keyof App, value: string | null) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch(`/api/proxy/track/${app.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ [field]: value }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      setLocal((prev) => ({ ...prev, [field]: value }));
      toast.success(
        value === null || value === ""
          ? `${fieldLabel(field)} cleared`
          : `${fieldLabel(field)} updated`
      );
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(`${fieldLabel(field)} save failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function patchMany(updates: Partial<App>): Promise<boolean> {
    if (busy) return false;
    setBusy(true);
    try {
      const res = await fetch(`/api/proxy/track/${app.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(updates),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      setLocal((prev) => ({ ...prev, ...updates }));
      const keys = Object.keys(updates) as (keyof App)[];
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
      const res = await fetch(`/api/proxy/track/${app.id}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success(`Deleted ${app.companyName}`);
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(`Delete failed: ${(e as Error).message}`);
      setBusy(false);
    }
  }

  const cellInput =
    "w-full bg-transparent outline-none focus:bg-base-200 rounded px-1 py-0.5 transition-colors";

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
          <CompanyCell
            name={local.companyName}
            url={local.companyCareerPage}
            onSave={(v) => patch("companyName", v || app.companyName)}
            className={cellInput}
          />
        </td>
        <td>
          <RoleCell
            role={local.jobRole ?? ""}
            url={local.jobUrl}
            onSave={(v) => patch("jobRole", v || null)}
            className={cellInput}
          />
        </td>
        <td>
          <EditableText
            value={local.location ?? ""}
            onSave={(v) => patch("location", v || null)}
            className={cellInput}
          />
        </td>
        <td className="min-w-[140px]">
          <ChipSelect
            value={local.interviewStatus ?? ""}
            options={INTERVIEW_OPTIONS}
            badgeMap={INTERVIEW_BADGE}
            emptyLabel="—"
            disabled={busy}
            onChange={(v) => patch("interviewStatus", v || null)}
          />
        </td>
        <td className="min-w-[140px]">
          <ChipSelect
            value={local.status}
            options={STATUS_OPTIONS}
            badgeMap={STATUS_BADGE}
            disabled={busy}
            onChange={(v) => patch("status", v)}
          />
        </td>
        <td>
          <input
            type="date"
            defaultValue={fmtDate(local.appliedDate)}
            onBlur={(e) => {
              if (e.target.value !== fmtDate(local.appliedDate)) {
                patch("appliedDate", e.target.value);
              }
            }}
            className="input input-ghost input-xs tabular-nums"
            disabled={busy}
          />
        </td>
        <td className="min-w-[180px]">
          <select
            value={local.resumeId ?? ""}
            onChange={(e) => patch("resumeId", e.target.value || null)}
            className="select select-bordered select-sm w-full"
            disabled={busy}
          >
            <option value="">—</option>
            {resumes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
            {local.resumeId &&
              !resumes.some((r) => r.id === local.resumeId) && (
                <option value={local.resumeId}>
                  {(local.resumeLabel ?? local.resumeId) + " (archived)"}
                </option>
              )}
          </select>
        </td>
        <td>
          <input
            type="date"
            defaultValue={fmtDate(local.decisionDate)}
            onBlur={(e) => {
              if (e.target.value !== fmtDate(local.decisionDate)) {
                patch("decisionDate", e.target.value || null);
              }
            }}
            className="input input-ghost input-xs tabular-nums"
            disabled={busy}
          />
        </td>
        <td>
          <EditableText
            value={local.decisionTime ?? ""}
            onSave={(v) => patch("decisionTime", v || null)}
            className={cellInput}
            placeholder="e.g. 2:00pm"
          />
        </td>
      </tr>
      {open && (
        <tr className="bg-base-200/40">
          <td colSpan={10} className="p-0 border-t border-base-300/40">
            <DetailsPanel
              app={local}
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

// ─── Details panel (the slide-down) ──────────────────────────────────────────

const DETAIL_FIELDS = [
  "companyCareerPage",
  "hrName",
  "hrEmail",
  "hrLinkedin",
  "referral",
  "referralLinkedin",
  "notes",
] as const;
type DetailField = (typeof DETAIL_FIELDS)[number];

type Draft = Record<DetailField, string>;

function appToDraft(app: App): Draft {
  return {
    companyCareerPage: app.companyCareerPage ?? "",
    hrName: app.hrName ?? "",
    hrEmail: app.hrEmail ?? "",
    hrLinkedin: app.hrLinkedin ?? "",
    referral: app.referral ?? "",
    referralLinkedin: app.referralLinkedin ?? "",
    notes: app.notes ?? "",
  };
}

function diffDraft(prev: Draft, next: Draft): Partial<App> {
  const out: Partial<App> = {};
  for (const k of DETAIL_FIELDS) {
    if (prev[k] !== next[k]) {
      // Empty string -> NULL on the wire so the column clears.
      out[k] = next[k] === "" ? null : next[k];
    }
  }
  return out;
}

function DetailsPanel({
  app,
  busy,
  onSaveMany,
  onDelete,
}: {
  app: App;
  busy: boolean;
  onSaveMany: (updates: Partial<App>) => Promise<boolean>;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [draft, setDraft] = useState<Draft>(() => appToDraft(app));

  // If the underlying app changes (e.g. after a successful save), refresh
  // the baseline draft when not actively editing.
  if (!editing) {
    const fresh = appToDraft(app);
    let same = true;
    for (const k of DETAIL_FIELDS) if (fresh[k] !== draft[k]) { same = false; break; }
    if (!same) setDraft(fresh);
  }

  const setField = (k: DetailField, v: string) =>
    setDraft((d) => ({ ...d, [k]: v }));

  const onEdit = () => setEditing(true);
  const onCancel = () => {
    setDraft(appToDraft(app));
    setEditing(false);
  };
  const onSave = async () => {
    const updates = diffDraft(appToDraft(app), draft);
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
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-base font-semibold tracking-tight">
            Application details
          </h2>
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
              Edit
            </button>
          )}
        </div>

        {/* Sections */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-6">
          <Section label="Career page" full>
            {editing ? (
              <UrlInput
                value={draft.companyCareerPage}
                onChange={(v) => setField("companyCareerPage", v)}
                placeholder="https://..."
              />
            ) : (
              <ReadUrl value={draft.companyCareerPage} />
            )}
          </Section>

          <Section label="HR contact">
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <Sub label="Name">
                {editing ? (
                  <TextInput
                    value={draft.hrName}
                    onChange={(v) => setField("hrName", v)}
                    placeholder="Add name"
                  />
                ) : (
                  <ReadText value={draft.hrName} />
                )}
              </Sub>
              <Sub label="Email">
                {editing ? (
                  <TextInput
                    value={draft.hrEmail}
                    onChange={(v) => setField("hrEmail", v)}
                    placeholder="Add email"
                    type="email"
                  />
                ) : (
                  <ReadEmail value={draft.hrEmail} />
                )}
              </Sub>
              <Sub label="LinkedIn">
                {editing ? (
                  <UrlInput
                    value={draft.hrLinkedin}
                    onChange={(v) => setField("hrLinkedin", v)}
                    placeholder="https://..."
                  />
                ) : (
                  <ReadUrl value={draft.hrLinkedin} compact />
                )}
              </Sub>
            </div>
          </Section>

          <Section label="Referral">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Sub label="Name">
                {editing ? (
                  <TextInput
                    value={draft.referral}
                    onChange={(v) => setField("referral", v)}
                    placeholder="Add name"
                  />
                ) : (
                  <ReadText value={draft.referral} />
                )}
              </Sub>
              <Sub label="LinkedIn">
                {editing ? (
                  <UrlInput
                    value={draft.referralLinkedin}
                    onChange={(v) => setField("referralLinkedin", v)}
                    placeholder="https://..."
                  />
                ) : (
                  <ReadUrl value={draft.referralLinkedin} compact />
                )}
              </Sub>
            </div>
          </Section>

          <Section label="Notes" full>
            {editing ? (
              <textarea
                value={draft.notes}
                onChange={(e) => setField("notes", e.target.value)}
                rows={3}
                placeholder="Add notes"
                className="textarea textarea-bordered textarea-sm w-full"
              />
            ) : (
              <ReadNotes value={draft.notes} />
            )}
          </Section>
        </div>

        {/* Footer */}
        <div className="flex justify-end items-center pt-5 mt-6 border-t border-base-300/40">
          {confirmingDelete ? (
            <div className="flex items-center gap-3">
              <span className="text-xs opacity-70">
                Delete <span className="font-medium">{app.companyName}</span>?
                This can&apos;t be undone.
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
              Delete application
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

function Sub({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <div className="text-[11px] uppercase tracking-wider opacity-50 mb-1 font-medium">
        {label}
      </div>
      {children}
    </div>
  );
}

// ─── Read-only display primitives ────────────────────────────────────────────

function ReadText({ value }: { value: string }) {
  if (!value) return <span className="text-sm opacity-30 italic">—</span>;
  return <span className="text-sm text-base-content">{value}</span>;
}

function ReadEmail({ value }: { value: string }) {
  if (!value) return <span className="text-sm opacity-30 italic">—</span>;
  return (
    <a href={`mailto:${value}`} className="link link-primary text-sm break-all">
      {value}
    </a>
  );
}

function ReadUrl({ value, compact }: { value: string; compact?: boolean }) {
  if (!value) return <span className="text-xs opacity-30 italic">—</span>;
  let pretty = value;
  try {
    const u = new URL(value);
    pretty = u.host + (u.pathname && u.pathname !== "/" ? u.pathname : "");
  } catch {
    /* keep raw */
  }
  const max = compact ? 36 : 64;
  if (pretty.length > max) pretty = pretty.slice(0, max - 1) + "…";
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

function ReadNotes({ value }: { value: string }) {
  if (!value)
    return <span className="text-sm opacity-30 italic">No notes</span>;
  return (
    <p className="text-sm whitespace-pre-wrap leading-relaxed text-base-content">
      {value}
    </p>
  );
}

// ─── Edit-mode input primitives ─────────────────────────────────────────────

function TextInput({
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="input input-bordered input-sm w-full"
    />
  );
}

function UrlInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <input
      type="url"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      className="input input-bordered input-sm w-full"
    />
  );
}

function CompanyCell({
  name,
  url,
  onSave,
  className,
}: {
  name: string;
  url: string | null;
  onSave: (v: string) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);

  if (editing || !url) {
    return (
      <input
        type="text"
        defaultValue={name}
        autoFocus={editing}
        onBlur={(e) => {
          setEditing(false);
          if (e.target.value !== name) onSave(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") setEditing(false);
        }}
        className={className}
      />
    );
  }

  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      onDoubleClick={(e) => {
        e.preventDefault();
        setEditing(true);
      }}
      title="Click to open posting · double-click to rename"
      className="link link-primary"
    >
      {name}
    </a>
  );
}

function ChipSelect({
  value,
  options,
  badgeMap,
  onChange,
  disabled,
  emptyLabel,
}: {
  value: string;
  options: string[];
  badgeMap: Record<string, string>;
  onChange: (v: string) => void;
  disabled?: boolean;
  emptyLabel?: string;
}) {
  const variant = badgeMap[value] ?? "badge-ghost";
  const label = value || emptyLabel || "";
  return (
    <div className="relative inline-block">
      <span
        className={`badge px-2.5 py-0.5 text-xs ${variant} ${!value ? "italic opacity-50" : ""}`}
      >
        {label}
      </span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed"
        aria-label="Set value"
      >
        {options.map((s) => (
          <option key={s || "_empty"} value={s}>
            {s || (emptyLabel ?? "(none)")}
          </option>
        ))}
      </select>
    </div>
  );
}

function EditableText({
  value,
  onSave,
  className,
  placeholder,
  multiline,
}: {
  value: string;
  onSave: (v: string) => void;
  className?: string;
  placeholder?: string;
  multiline?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(value);

  const commit = () => {
    setEditing(false);
    if (v !== value) onSave(v);
  };

  if (!editing) {
    const display = value || placeholder || "Add";
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
        {display}
      </span>
    );
  }

  if (multiline) {
    return (
      <textarea
        value={v}
        rows={2}
        autoFocus
        placeholder={placeholder}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        className="textarea textarea-bordered textarea-sm w-full min-w-[200px] glow-ring"
      />
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

function RoleCell({
  role,
  url,
  onSave,
  className,
}: {
  role: string;
  url: string | null;
  onSave: (v: string) => void;
  className?: string;
}) {
  const [editing, setEditing] = useState(false);
  const [v, setV] = useState(role);

  const commit = () => {
    setEditing(false);
    if (v !== role) onSave(v);
  };

  if (editing) {
    return (
      <input
        type="text"
        value={v}
        autoFocus
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setV(role);
            setEditing(false);
          }
        }}
        className={className}
      />
    );
  }

  if (url) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="link link-primary text-sm"
        title="Click to open · double-click to edit"
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setV(role);
          setEditing(true);
        }}
      >
        {role || "View posting"}
      </a>
    );
  }

  return (
    <span
      onClick={() => {
        setV(role);
        setEditing(true);
      }}
      className={`block cursor-text rounded px-1 py-0.5 hover:bg-base-300/40 transition-colors ${
        role ? "" : "opacity-40 italic text-xs"
      }`}
      title="Click to edit"
    >
      {role || "Add role"}
    </span>
  );
}
