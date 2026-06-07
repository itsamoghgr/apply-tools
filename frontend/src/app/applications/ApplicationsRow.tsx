"use client";

import { memo, useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Pencil,
  ExternalLink,
  Trash2,
  Mail,
  Plus,
  Send,
  X as XIcon,
  Sparkles,
  Copy,
  Check,
} from "lucide-react";
import LeadPicker from "./LeadPicker";
import type { LinkedLead, AppReachOut } from "./ApplicationsTable";

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
  jobDescription: string | null;
  linkedLeads: LinkedLead[];
  reachOuts: AppReachOut[];
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
  jobDescription: "Job description",
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

function ApplicationsRowInner({
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
  const [pickerMode, setPickerMode] = useState<
    "off" | "link" | "reach-out"
  >("off");

  // Re-sync local state when the parent re-renders with fresh props
  // (e.g., after router.refresh()). We compare by reference; the page
  // re-serialises rows on every refresh so identity changes correctly.
  const lastAppRef = useRef(app);
  useEffect(() => {
    if (lastAppRef.current !== app) {
      lastAppRef.current = app;
      setLocal(app);
    }
  }, [app]);

  function navigateToReachOut(leadId: string) {
    router.push(
      `/reach-out?leadId=${encodeURIComponent(
        leadId
      )}&applicationId=${encodeURIComponent(app.id)}`
    );
  }

  function onMainReachOutClick() {
    if (local.linkedLeads.length === 1) {
      navigateToReachOut(local.linkedLeads[0].id);
      return;
    }
    setPickerMode("reach-out");
  }

  async function linkLead(
    leadId: string,
    role: string | null
  ): Promise<LinkedLead> {
    const res = await fetch(`/api/proxy/track/${app.id}/leads`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ leadId, role }),
    });
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `HTTP ${res.status}`);
    }
    const json = (await res.json()) as { lead: LinkedLead };
    return json.lead;
  }

  function mergeLinkedLead(lead: LinkedLead) {
    setLocal((prev) =>
      prev.linkedLeads.some((l) => l.id === lead.id)
        ? prev
        : { ...prev, linkedLeads: [...prev.linkedLeads, lead] }
    );
  }

  async function onPickForLink(leadId: string, role: string | null) {
    try {
      const lead = await linkLead(leadId, role);
      mergeLinkedLead(lead);
      toast.success("Lead linked");
      setPickerMode("off");
      // No router.refresh(): mergeLinkedLead already updated this row's
      // local state. Refreshing would re-run the 800-row Prisma query
      // for one inline link change.
    } catch (e) {
      toast.error(`Link failed: ${(e as Error).message}`);
    }
  }

  async function onPickForReachOut(leadId: string, role: string | null) {
    try {
      // If this lead isn't already linked, link it on the way through.
      if (!local.linkedLeads.some((l) => l.id === leadId)) {
        const lead = await linkLead(leadId, role);
        mergeLinkedLead(lead);
      }
      setPickerMode("off");
      navigateToReachOut(leadId);
    } catch (e) {
      toast.error(`Could not start reach-out: ${(e as Error).message}`);
    }
  }

  async function unlinkLead(leadId: string) {
    if (busy) return;
    setBusy(true);
    try {
      const res = await fetch(
        `/api/proxy/track/${app.id}/leads/${leadId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      toast.success("Lead unlinked");
      setLocal((prev) => ({
        ...prev,
        linkedLeads: prev.linkedLeads.filter((l) => l.id !== leadId),
      }));
      // No refresh: local filter is the only visible change for this row.
    } catch (e) {
      toast.error(`Unlink failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

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
      // No router.refresh(): the row's setLocal above already reflects
      // the change. router.refresh() here would re-run the full Prisma
      // query for every keystroke-blur on every cell.
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
      // No router.refresh(): setLocal already reflects every patched field.
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
        <td className="text-right pr-4">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onMainReachOutClick();
            }}
            disabled={busy}
            title={
              local.linkedLeads.length === 0
                ? "Pick a lead to reach out to"
                : local.linkedLeads.length === 1
                ? `Reach out to ${local.linkedLeads[0].name}`
                : "Choose which lead to reach out to"
            }
            className="inline-flex items-center gap-1.5 text-xs font-medium text-primary/80 hover:text-primary hover:bg-primary/10 rounded-md px-2.5 py-1.5 transition-colors disabled:opacity-40"
          >
            <Mail className="h-3.5 w-3.5" />
            Reach out
            {local.linkedLeads.length > 1 && (
              <span className="badge badge-ghost badge-xs ml-0.5">
                {local.linkedLeads.length}
              </span>
            )}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-base-200/40">
          <td colSpan={11} className="p-0 border-t border-base-300/40">
            <DetailsPanel
              app={local}
              busy={busy}
              onSaveMany={patchMany}
              onDelete={onDelete}
              onUnlinkLead={unlinkLead}
              onLinkLead={() => setPickerMode("link")}
              onReachOutToLead={navigateToReachOut}
            />
          </td>
        </tr>
      )}
      {pickerMode !== "off" && (
        <PickerPortal
          mode={pickerMode}
          alreadyLinkedIds={local.linkedLeads.map((l) => l.id)}
          onPick={
            pickerMode === "link" ? onPickForLink : onPickForReachOut
          }
          onClose={() => setPickerMode("off")}
        />
      )}
    </>
  );
}

// React.memo so typing in the search box doesn't re-render the ~200 rows
// whose `app` reference didn't change. Reference identity is stable for
// unchanged rows because the parent maps over the same serialised array.
const ApplicationsRow = memo(ApplicationsRowInner);
export default ApplicationsRow;

function PickerPortal({
  mode,
  alreadyLinkedIds,
  onPick,
  onClose,
}: {
  mode: "link" | "reach-out";
  alreadyLinkedIds: string[];
  onPick: (leadId: string, role: string | null) => Promise<void> | void;
  onClose: () => void;
}) {
  // For "reach-out" we let the user pick from any lead (linked or not).
  // For "link" we hide already-linked leads.
  const exclude = mode === "link" ? alreadyLinkedIds : [];
  const title =
    mode === "link" ? "Link a lead to this application" : "Reach out to…";
  return (
    <LeadPicker
      title={title}
      excludeLeadIds={exclude}
      onPick={onPick}
      onClose={onClose}
      showRoleField={mode === "link"}
    />
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
  "jobDescription",
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
    jobDescription: app.jobDescription ?? "",
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
  onUnlinkLead,
  onLinkLead,
  onReachOutToLead,
}: {
  app: App;
  busy: boolean;
  onSaveMany: (updates: Partial<App>) => Promise<boolean>;
  onDelete: () => void;
  onUnlinkLead: (leadId: string) => void;
  onLinkLead: () => void;
  onReachOutToLead: (leadId: string) => void;
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
          <Section label="Linked leads" full>
            <LinkedLeadsList
              leads={app.linkedLeads}
              busy={busy}
              onLinkLead={onLinkLead}
              onUnlinkLead={onUnlinkLead}
              onReachOutToLead={onReachOutToLead}
            />
          </Section>

          <Section label="Outreach history" full>
            <OutreachHistoryList reachOuts={app.reachOuts} />
          </Section>

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

          <Section label="Job description" full>
            {editing ? (
              <textarea
                value={draft.jobDescription}
                onChange={(e) => setField("jobDescription", e.target.value)}
                rows={8}
                placeholder="Paste the job description"
                className="textarea textarea-bordered textarea-sm w-full font-mono text-xs"
              />
            ) : (
              <ReadJobDescription value={draft.jobDescription} />
            )}
          </Section>

          <Section label="Ask a question" full>
            <QuestionSection
              company={app.companyName}
              jobDescription={draft.jobDescription}
              resumeId={app.resumeId}
            />
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

function LinkedLeadsList({
  leads,
  busy,
  onLinkLead,
  onUnlinkLead,
  onReachOutToLead,
}: {
  leads: LinkedLead[];
  busy: boolean;
  onLinkLead: () => void;
  onUnlinkLead: (leadId: string) => void;
  onReachOutToLead: (leadId: string) => void;
}) {
  return (
    <div className="space-y-2">
      {leads.length === 0 ? (
        <p className="text-sm opacity-30 italic">
          No leads linked yet.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-2">
          {leads.map((l) => (
            <li
              key={l.id}
              className="inline-flex items-center gap-2 rounded-full bg-base-100 border border-base-300/60 pl-3 pr-1 py-1"
            >
              <span className="text-sm font-medium">{l.name}</span>
              {l.linkRole && (
                <span className="badge badge-ghost badge-xs">
                  {l.linkRole}
                </span>
              )}
              {l.email && (
                <span className="text-xs opacity-50 truncate max-w-[180px]">
                  {l.email}
                </span>
              )}
              <button
                type="button"
                onClick={() => onReachOutToLead(l.id)}
                className="inline-flex items-center justify-center h-6 w-6 rounded-full text-primary/80 hover:text-primary hover:bg-primary/10 transition-colors"
                title={`Reach out to ${l.name}`}
              >
                <Send className="h-3 w-3" />
              </button>
              <button
                type="button"
                onClick={() => onUnlinkLead(l.id)}
                disabled={busy}
                className="inline-flex items-center justify-center h-6 w-6 rounded-full opacity-50 hover:opacity-100 hover:bg-error/10 hover:text-error transition-colors disabled:opacity-30"
                title="Unlink"
              >
                <XIcon className="h-3 w-3" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <button
        type="button"
        onClick={onLinkLead}
        className="inline-flex items-center gap-1.5 text-xs font-medium text-primary/80 hover:text-primary hover:bg-primary/10 rounded-md px-2.5 py-1.5 transition-colors"
      >
        <Plus className="h-3.5 w-3.5" />
        Link a lead
      </button>
    </div>
  );
}

const REACH_OUT_STATUS_BADGE: Record<string, string> = {
  draft: "badge-ghost",
  sent: "badge-success",
  failed: "badge-error",
};

function OutreachHistoryList({ reachOuts }: { reachOuts: AppReachOut[] }) {
  if (reachOuts.length === 0) {
    return (
      <p className="text-sm opacity-30 italic">
        No reach-outs recorded for this application yet.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {reachOuts.map((r) => {
        const variant =
          REACH_OUT_STATUS_BADGE[r.status] ?? "badge-ghost";
        const dateIso = r.sentAt ?? r.createdAt;
        const date = new Date(dateIso);
        const dateStr = isNaN(date.getTime())
          ? ""
          : date.toLocaleDateString();
        return (
          <li
            key={r.id}
            className="flex items-center gap-3 rounded-lg bg-base-100 border border-base-300/40 px-3 py-2"
          >
            <span className={`badge ${variant} badge-xs shrink-0`}>
              {r.status}
            </span>
            <a
              href={`/reach-out?edit=${encodeURIComponent(r.id)}`}
              className="link link-hover text-sm flex-1 min-w-0 truncate"
              title={r.subject}
            >
              {r.subject || <span className="opacity-50 italic">(no subject)</span>}
            </a>
            <span className="text-xs opacity-50 shrink-0">
              {r.recipientName}
            </span>
            {dateStr && (
              <span className="text-xs opacity-40 shrink-0 tabular-nums">
                {dateStr}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function ReadJobDescription({ value }: { value: string }) {
  const [expanded, setExpanded] = useState(false);
  if (!value)
    return (
      <span className="text-sm opacity-30 italic">
        No job description saved
      </span>
    );

  const COLLAPSED_LIMIT = 600;
  const isLong = value.length > COLLAPSED_LIMIT;
  const display = expanded || !isLong ? value : value.slice(0, COLLAPSED_LIMIT);

  return (
    <div className="space-y-2">
      <p className="text-sm whitespace-pre-wrap leading-relaxed text-base-content/90">
        {display}
        {isLong && !expanded && <span className="opacity-50">…</span>}
      </p>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs font-medium text-primary/80 hover:text-primary transition-colors"
        >
          {expanded ? "Show less" : `Show full (${value.length} chars)`}
        </button>
      )}
    </div>
  );
}

// ─── Ask-a-question (application form answers) ──────────────────────────────

function QuestionSection({
  company,
  jobDescription,
  resumeId,
}: {
  company: string;
  jobDescription: string;
  resumeId: string | null;
}) {
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const hasJd = jobDescription.trim().length > 0;
  const canAsk = hasJd && question.trim().length > 0 && !loading;

  async function ask() {
    if (!canAsk) return;
    setLoading(true);
    setAnswer("");
    setCopied(false);
    try {
      const res = await fetch(`/api/proxy/answer-question`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          company,
          job_description: jobDescription,
          question: question.trim(),
          resume_id: resumeId,
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const json = (await res.json()) as { answer?: string };
      setAnswer(json.answer ?? "");
    } catch (e) {
      toast.error(`Couldn't answer: ${(e as Error).message}`);
    } finally {
      setLoading(false);
    }
  }

  async function copyAnswer() {
    try {
      await navigator.clipboard.writeText(answer);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Copy failed");
    }
  }

  return (
    <div className="space-y-3">
      {!hasJd && (
        <p className="text-xs opacity-50 italic">
          Add a job description above to answer application questions in context.
        </p>
      )}
      <textarea
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={(e) => {
          // Cmd/Ctrl+Enter submits, matching the rest of the app's textareas.
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            ask();
          }
        }}
        rows={2}
        placeholder='e.g. "Why do you want to work here?" or "Describe a time you handled conflicting priorities."'
        disabled={!hasJd}
        className="textarea textarea-bordered textarea-sm w-full"
      />
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={ask}
          disabled={!canAsk}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary-content bg-primary hover:bg-primary/90 rounded-md px-3 py-1.5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Sparkles className="h-3.5 w-3.5" />
          {loading ? "Answering…" : "Answer"}
        </button>
        <span className="text-[11px] opacity-40">⌘/Ctrl + Enter</span>
      </div>

      {answer && (
        <div className="rounded-lg bg-base-100 border border-base-300/60 p-3 space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-[11px] uppercase tracking-wider opacity-50 font-medium">
              Answer
            </span>
            <button
              type="button"
              onClick={copyAnswer}
              className="inline-flex items-center gap-1 text-xs opacity-60 hover:opacity-100 hover:text-primary transition-colors"
              title="Copy answer"
            >
              {copied ? (
                <>
                  <Check className="h-3 w-3" /> Copied
                </>
              ) : (
                <>
                  <Copy className="h-3 w-3" /> Copy
                </>
              )}
            </button>
          </div>
          <p className="text-sm whitespace-pre-wrap leading-relaxed text-base-content">
            {answer}
          </p>
        </div>
      )}
    </div>
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
      <span className="group inline-flex items-center gap-1 min-w-0">
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="link link-primary text-sm truncate"
          title="Click to open posting · double-click to edit"
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
        <button
          type="button"
          title="Edit role"
          aria-label="Edit role"
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            setV(role);
            setEditing(true);
          }}
          className="shrink-0 opacity-0 group-hover:opacity-60 hover:!opacity-100 hover:text-primary transition-opacity p-0.5 rounded"
        >
          <Pencil className="h-3 w-3" />
        </button>
      </span>
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
