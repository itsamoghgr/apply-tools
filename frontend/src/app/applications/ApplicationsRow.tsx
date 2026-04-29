"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

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
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(`Save failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!confirm(`Delete ${app.companyName}?`)) return;
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
          <td colSpan={10} className="px-8 py-6 border-t border-base-300/40">
            <div className="space-y-6 max-w-5xl animate-fade-in">
              <div>
                <DetailLabel>Career page</DetailLabel>
                <UrlField
                  value={local.companyCareerPage ?? ""}
                  onSave={(v) => patch("companyCareerPage", v || null)}
                />
              </div>

              <div>
                <DetailLabel>HR contact</DetailLabel>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-x-6 gap-y-3">
                  <SubField label="Name">
                    <EditableText
                      value={local.hrName ?? ""}
                      onSave={(v) => patch("hrName", v || null)}
                      className={cellInput}
                      placeholder="Add name"
                    />
                  </SubField>
                  <SubField label="Email">
                    <EditableText
                      value={local.hrEmail ?? ""}
                      onSave={(v) => patch("hrEmail", v || null)}
                      className={cellInput}
                      placeholder="Add email"
                    />
                  </SubField>
                  <SubField label="LinkedIn">
                    <UrlField
                      value={local.hrLinkedin ?? ""}
                      onSave={(v) => patch("hrLinkedin", v || null)}
                      compact
                    />
                  </SubField>
                </div>
              </div>

              <div>
                <DetailLabel>Referral</DetailLabel>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-3">
                  <SubField label="Name">
                    <EditableText
                      value={local.referral ?? ""}
                      onSave={(v) => patch("referral", v || null)}
                      className={cellInput}
                      placeholder="Add name"
                    />
                  </SubField>
                  <SubField label="LinkedIn">
                    <UrlField
                      value={local.referralLinkedin ?? ""}
                      onSave={(v) => patch("referralLinkedin", v || null)}
                      compact
                    />
                  </SubField>
                </div>
              </div>

              <div>
                <DetailLabel>Notes</DetailLabel>
                <EditableText
                  value={local.notes ?? ""}
                  onSave={(v) => patch("notes", v || null)}
                  className={cellInput}
                  placeholder="Add notes"
                  multiline
                />
              </div>

              <div className="flex justify-end pt-3 border-t border-base-300/40">
                <button
                  onClick={onDelete}
                  disabled={busy}
                  className="btn btn-error btn-outline btn-xs hover:shadow-[0_0_12px_-2px] hover:shadow-error/40 transition-shadow"
                >
                  Delete this application
                </button>
              </div>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function DetailLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase tracking-widest opacity-40 mb-2 font-semibold">
      {children}
    </div>
  );
}

function SubField({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="text-[10px] opacity-50 mb-0.5">{label}</div>
      <div>{children}</div>
    </div>
  );
}

function UrlField({
  value,
  onSave,
  compact,
}: {
  value: string;
  onSave: (v: string) => void;
  compact?: boolean;
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
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") (e.target as HTMLInputElement).blur();
          if (e.key === "Escape") {
            setV(value);
            setEditing(false);
          }
        }}
        placeholder="https://..."
        className="input input-bordered input-sm w-full glow-ring"
      />
    );
  }

  if (!value) {
    return (
      <button
        onClick={() => {
          setV("");
          setEditing(true);
        }}
        className="text-xs opacity-40 hover:opacity-70 italic transition-opacity"
      >
        Add link
      </button>
    );
  }

  let pretty = value;
  try {
    const u = new URL(value);
    pretty = u.host + (u.pathname && u.pathname !== "/" ? u.pathname : "");
    if (pretty.length > (compact ? 32 : 60)) {
      pretty = pretty.slice(0, compact ? 30 : 58) + "…";
    }
  } catch {
    if (pretty.length > (compact ? 32 : 60)) {
      pretty = pretty.slice(0, compact ? 30 : 58) + "…";
    }
  }

  return (
    <div className="flex items-center gap-2 min-w-0">
      <a
        href={value}
        target="_blank"
        rel="noopener noreferrer"
        className="link link-primary text-sm truncate"
        title={value}
      >
        {pretty}
      </a>
      <button
        onClick={() => {
          setV(value);
          setEditing(true);
        }}
        className="text-[10px] opacity-40 hover:opacity-70 shrink-0 transition-opacity"
        title="Edit URL"
      >
        edit
      </button>
    </div>
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
