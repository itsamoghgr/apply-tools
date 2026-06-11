"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Plus,
  Trash2,
  Save,
  Loader2,
  ChevronUp,
  ChevronDown,
  X,
} from "lucide-react";
import Modal from "../resume-builder/Modal";
import {
  addExperience,
  updateExperience,
  deleteExperience,
  reorderExperiences,
  addProject,
  updateProject,
  deleteProject,
  reorderProjects,
  addSkill,
  updateSkill,
  deleteSkill,
  reorderSkills,
  saveProfileHeader,
} from "./actions";
import type {
  ProfileData,
  ProfileHeader,
  ExperiencePayload,
  ProjectPayload,
  SkillPayload,
} from "./types";

// MutationResult mirrors the server-action return shape. Surfacing the error
// string to a toast is the whole point of this type here.
type MutationResult = { ok: true } | { ok: false; error: string };

// ── small primitives (match resume-builder conventions) ─────────────────────

function SectionCard({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="glass-card p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">{title}</h2>
        {action}
      </div>
      {children}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  type?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium opacity-60">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="input input-bordered input-sm w-full mt-1"
      />
    </label>
  );
}

function Empty({ label }: { label: string }) {
  return <p className="text-sm opacity-40 py-2">{label}</p>;
}

// Up/Down reorder controls. The first/last entry has the matching arrow
// disabled. Delete sits alongside so each entry's controls are in one cluster.
function EntryControls({
  onUp,
  onDown,
  onEdit,
  onDelete,
  isFirst,
  isLast,
}: {
  onUp: () => void;
  onDown: () => void;
  onEdit: () => void;
  onDelete: () => void;
  isFirst: boolean;
  isLast: boolean;
}) {
  return (
    <div className="flex items-center gap-0.5">
      <button
        type="button"
        onClick={onUp}
        disabled={isFirst}
        title="Move up"
        aria-label="Move up"
        className="btn btn-ghost btn-xs btn-square disabled:opacity-20"
      >
        <ChevronUp className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={onDown}
        disabled={isLast}
        title="Move down"
        aria-label="Move down"
        className="btn btn-ghost btn-xs btn-square disabled:opacity-20"
      >
        <ChevronDown className="h-3.5 w-3.5" />
      </button>
      <button
        type="button"
        onClick={onEdit}
        className="btn btn-ghost btn-xs"
      >
        Edit
      </button>
      <button
        type="button"
        onClick={onDelete}
        title="Delete"
        aria-label="Delete"
        className="btn btn-ghost btn-xs btn-square text-error/70 hover:bg-error/10"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// Editable list of bullet lines (used by experience & project modals). Each
// row is a text input with a remove button; an "Add line" button appends.
function BulletList({
  bullets,
  onChange,
}: {
  bullets: string[];
  onChange: (b: string[]) => void;
}) {
  return (
    <div className="space-y-2">
      <span className="text-xs font-medium opacity-60">Bullets</span>
      {bullets.length === 0 && (
        <p className="text-xs opacity-40">No bullets yet.</p>
      )}
      <div className="space-y-2">
        {bullets.map((b, i) => (
          <div key={i} className="flex items-start gap-2">
            <textarea
              value={b}
              onChange={(e) =>
                onChange(bullets.map((x, j) => (j === i ? e.target.value : x)))
              }
              rows={2}
              placeholder="Describe an accomplishment — lead with impact and a metric."
              className="textarea textarea-bordered textarea-sm w-full resize-y text-sm"
            />
            <button
              type="button"
              onClick={() => onChange(bullets.filter((_, j) => j !== i))}
              title="Remove line"
              aria-label="Remove line"
              className="btn btn-ghost btn-xs btn-square text-error/70 mt-0.5"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => onChange([...bullets, ""])}
        className="btn btn-ghost btn-xs gap-1"
      >
        <Plus className="h-3 w-3" /> Add line
      </button>
    </div>
  );
}

// ── main editor ─────────────────────────────────────────────────────────────

export default function ProfileEditor({
  initialProfile,
}: {
  initialProfile: ProfileData;
}) {
  const router = useRouter();

  // The lists are read straight from props; after every successful mutation we
  // call router.refresh() so the server-rendered data (which the actions
  // revalidate) flows back down. Header is the one section with local draft
  // state, since it's free-form and saved explicitly.
  const { experiences, projects, skills } = initialProfile;

  // Runs a server action, toasts on failure, and refreshes on success so the
  // revalidated server data re-hydrates the lists.
  async function run(
    action: () => Promise<MutationResult>,
    success?: string,
  ): Promise<boolean> {
    const res = await action();
    if (res.ok) {
      if (success) toast.success(success);
      router.refresh();
      return true;
    }
    toast.error(res.error);
    return false;
  }

  // Modal state: which kind of entry is being added/edited, and the row id when
  // editing (null = adding a new one).
  const [expModal, setExpModal] = useState<{ id: string | null } | null>(null);
  const [projModal, setProjModal] = useState<{ id: string | null } | null>(
    null,
  );
  const [skillModal, setSkillModal] = useState<{ id: string | null } | null>(
    null,
  );

  // Reorder by swapping two indices and persisting the full id order.
  function move<T extends { id: string }>(
    list: T[],
    from: number,
    to: number,
    reorder: (ids: string[]) => Promise<MutationResult>,
  ) {
    if (to < 0 || to >= list.length) return;
    const ids = list.map((x) => x.id);
    [ids[from], ids[to]] = [ids[to], ids[from]];
    run(() => reorder(ids));
  }

  return (
    <div className="space-y-5">
      <HeaderSection initial={initialProfile} run={run} />

      {/* Experiences */}
      <SectionCard
        title="Experience"
        action={
          <button
            type="button"
            onClick={() => setExpModal({ id: null })}
            className="btn btn-ghost btn-xs gap-1"
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </button>
        }
      >
        {experiences.length === 0 && (
          <Empty label="No experience entries yet." />
        )}
        <div className="space-y-3">
          {experiences.map((e, i) => (
            <div
              key={e.id}
              className="rounded-xl border border-base-300 bg-base-200/30 p-4 flex items-start justify-between gap-3"
            >
              <div className="min-w-0 space-y-1">
                <div className="font-medium truncate">
                  {e.title || "Untitled role"}
                  {e.company && (
                    <span className="opacity-60"> · {e.company}</span>
                  )}
                </div>
                <div className="text-xs opacity-60 font-mono">
                  {[e.startDate, e.endDate].filter(Boolean).join(" – ")}
                  {e.location && (
                    <span className="font-sans"> · {e.location}</span>
                  )}
                </div>
                {e.bullets.length > 0 && (
                  <ul className="text-sm opacity-70 list-disc pl-4 space-y-0.5 mt-1.5">
                    {e.bullets.slice(0, 2).map((b, j) => (
                      <li key={j} className="truncate">
                        {b}
                      </li>
                    ))}
                    {e.bullets.length > 2 && (
                      <li className="opacity-50 list-none">
                        +{e.bullets.length - 2} more
                      </li>
                    )}
                  </ul>
                )}
              </div>
              <EntryControls
                isFirst={i === 0}
                isLast={i === experiences.length - 1}
                onUp={() =>
                  move(experiences, i, i - 1, reorderExperiences)
                }
                onDown={() =>
                  move(experiences, i, i + 1, reorderExperiences)
                }
                onEdit={() => setExpModal({ id: e.id })}
                onDelete={() => {
                  if (confirm("Delete this experience?"))
                    run(() => deleteExperience(e.id), "Experience deleted");
                }}
              />
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Projects */}
      <SectionCard
        title="Projects"
        action={
          <button
            type="button"
            onClick={() => setProjModal({ id: null })}
            className="btn btn-ghost btn-xs gap-1"
          >
            <Plus className="h-3.5 w-3.5" /> Add
          </button>
        }
      >
        {projects.length === 0 && <Empty label="No projects yet." />}
        <div className="space-y-3">
          {projects.map((p, i) => (
            <div
              key={p.id}
              className="rounded-xl border border-base-300 bg-base-200/30 p-4 flex items-start justify-between gap-3"
            >
              <div className="min-w-0 space-y-1">
                <div className="font-medium truncate">
                  {p.name || "Untitled project"}
                  {p.date && (
                    <span className="opacity-60 font-mono text-xs">
                      {" "}
                      · {p.date}
                    </span>
                  )}
                </div>
                {p.link && (
                  <div className="text-xs text-primary truncate">{p.link}</div>
                )}
                {p.bullets.length > 0 && (
                  <ul className="text-sm opacity-70 list-disc pl-4 space-y-0.5 mt-1.5">
                    {p.bullets.slice(0, 2).map((b, j) => (
                      <li key={j} className="truncate">
                        {b}
                      </li>
                    ))}
                    {p.bullets.length > 2 && (
                      <li className="opacity-50 list-none">
                        +{p.bullets.length - 2} more
                      </li>
                    )}
                  </ul>
                )}
              </div>
              <EntryControls
                isFirst={i === 0}
                isLast={i === projects.length - 1}
                onUp={() => move(projects, i, i - 1, reorderProjects)}
                onDown={() => move(projects, i, i + 1, reorderProjects)}
                onEdit={() => setProjModal({ id: p.id })}
                onDelete={() => {
                  if (confirm("Delete this project?"))
                    run(() => deleteProject(p.id), "Project deleted");
                }}
              />
            </div>
          ))}
        </div>
      </SectionCard>

      {/* Skills */}
      <SectionCard
        title="Skills"
        action={
          <button
            type="button"
            onClick={() => setSkillModal({ id: null })}
            className="btn btn-ghost btn-xs gap-1"
          >
            <Plus className="h-3.5 w-3.5" /> Add category
          </button>
        }
      >
        {skills.length === 0 && <Empty label="No skill categories yet." />}
        <div className="space-y-3">
          {skills.map((s, i) => (
            <div
              key={s.id}
              className="rounded-xl border border-base-300 bg-base-200/30 p-4 flex items-start justify-between gap-3"
            >
              <div className="min-w-0 space-y-2">
                <div className="font-medium">
                  {s.category || "Uncategorized"}
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {s.items.length === 0 && (
                    <span className="text-xs opacity-40">No items.</span>
                  )}
                  {s.items.map((it, j) => (
                    <span key={j} className="badge badge-ghost">
                      {it}
                    </span>
                  ))}
                </div>
              </div>
              <EntryControls
                isFirst={i === 0}
                isLast={i === skills.length - 1}
                onUp={() => move(skills, i, i - 1, reorderSkills)}
                onDown={() => move(skills, i, i + 1, reorderSkills)}
                onEdit={() => setSkillModal({ id: s.id })}
                onDelete={() => {
                  if (confirm("Delete this skill category?"))
                    run(() => deleteSkill(s.id), "Skill category deleted");
                }}
              />
            </div>
          ))}
        </div>
      </SectionCard>

      {expModal && (
        <ExperienceModal
          entry={
            expModal.id
              ? experiences.find((e) => e.id === expModal.id) ?? null
              : null
          }
          onClose={() => setExpModal(null)}
          run={run}
        />
      )}
      {projModal && (
        <ProjectModal
          entry={
            projModal.id
              ? projects.find((p) => p.id === projModal.id) ?? null
              : null
          }
          onClose={() => setProjModal(null)}
          run={run}
        />
      )}
      {skillModal && (
        <SkillModal
          entry={
            skillModal.id
              ? skills.find((s) => s.id === skillModal.id) ?? null
              : null
          }
          onClose={() => setSkillModal(null)}
          run={run}
        />
      )}
    </div>
  );
}

// ── header (contact) section ────────────────────────────────────────────────

function HeaderSection({
  initial,
  run,
}: {
  initial: ProfileHeader;
  run: (
    action: () => Promise<MutationResult>,
    success?: string,
  ) => Promise<boolean>;
}) {
  // Header keeps local draft state and saves explicitly, like resume-builder.
  const [draft, setDraft] = useState<ProfileHeader>({
    fullName: initial.fullName,
    email: initial.email,
    phone: initial.phone,
    location: initial.location,
    linkedin: initial.linkedin,
    github: initial.github,
    portfolio: initial.portfolio,
  });
  const [dirty, setDirty] = useState(false);
  const [saving, startSave] = useTransition();

  function set<K extends keyof ProfileHeader>(key: K, val: string) {
    setDraft((d) => ({ ...d, [key]: val }));
    setDirty(true);
  }

  function save() {
    startSave(async () => {
      const ok = await run(() => saveProfileHeader(draft), "Profile saved");
      if (ok) setDirty(false);
    });
  }

  return (
    <SectionCard
      title="Contact"
      action={
        <button
          type="button"
          onClick={save}
          disabled={saving || !dirty}
          className="btn btn-outline btn-sm gap-1.5"
        >
          {saving ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {dirty ? "Save" : "Saved"}
        </button>
      }
    >
      <div className="grid sm:grid-cols-2 gap-3">
        <Field
          label="Full name"
          value={draft.fullName}
          onChange={(v) => set("fullName", v)}
          placeholder="Amogh Ramagiri"
        />
        <Field
          label="Location"
          value={draft.location}
          onChange={(v) => set("location", v)}
          placeholder="Arlington, VA"
        />
        <Field
          label="Email"
          value={draft.email}
          onChange={(v) => set("email", v)}
          placeholder="you@email.com"
        />
        <Field
          label="Phone"
          value={draft.phone}
          onChange={(v) => set("phone", v)}
          placeholder="571-478-2290"
        />
        <Field
          label="LinkedIn URL"
          value={draft.linkedin}
          onChange={(v) => set("linkedin", v)}
          placeholder="https://linkedin.com/in/…"
        />
        <Field
          label="GitHub URL"
          value={draft.github}
          onChange={(v) => set("github", v)}
          placeholder="https://github.com/…"
        />
        <Field
          label="Portfolio URL"
          value={draft.portfolio}
          onChange={(v) => set("portfolio", v)}
          placeholder="https://…"
        />
      </div>
    </SectionCard>
  );
}

// ── add/edit modals ─────────────────────────────────────────────────────────

function ModalFooter({
  pending,
  isEdit,
  onClose,
  onSave,
}: {
  pending: boolean;
  isEdit: boolean;
  onClose: () => void;
  onSave: () => void;
}) {
  return (
    <div className="flex justify-end gap-2">
      <button
        type="button"
        className="btn btn-ghost btn-sm"
        onClick={onClose}
        disabled={pending}
      >
        Cancel
      </button>
      <button
        type="button"
        className="btn btn-gradient btn-sm gap-1.5"
        onClick={onSave}
        disabled={pending}
      >
        {pending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Save className="h-4 w-4" />
        )}
        {isEdit ? "Save changes" : "Add"}
      </button>
    </div>
  );
}

function ExperienceModal({
  entry,
  onClose,
  run,
}: {
  entry: (ExperiencePayload & { id: string }) | null;
  onClose: () => void;
  run: (
    action: () => Promise<MutationResult>,
    success?: string,
  ) => Promise<boolean>;
}) {
  const [form, setForm] = useState<ExperiencePayload>({
    company: entry?.company ?? "",
    title: entry?.title ?? "",
    location: entry?.location ?? "",
    startDate: entry?.startDate ?? "",
    endDate: entry?.endDate ?? "",
    bullets: entry?.bullets ?? [],
  });
  const [pending, setPending] = useState(false);

  function set<K extends keyof ExperiencePayload>(
    key: K,
    val: ExperiencePayload[K],
  ) {
    setForm((f) => ({ ...f, [key]: val }));
  }

  async function save() {
    setPending(true);
    const ok = await run(
      () =>
        entry
          ? updateExperience(entry.id, form)
          : addExperience(form),
      entry ? "Experience updated" : "Experience added",
    );
    setPending(false);
    if (ok) onClose();
  }

  return (
    <Modal
      title={entry ? "Edit experience" : "Add experience"}
      onClose={onClose}
      maxWidth="max-w-2xl"
    >
      <div className="grid sm:grid-cols-2 gap-3">
        <Field
          label="Title"
          value={form.title}
          onChange={(v) => set("title", v)}
          placeholder="Software Engineer"
        />
        <Field
          label="Company"
          value={form.company}
          onChange={(v) => set("company", v)}
          placeholder="Acme Corp"
        />
        <Field
          label="Location"
          value={form.location}
          onChange={(v) => set("location", v)}
          placeholder="Remote"
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Start date"
            value={form.startDate}
            onChange={(v) => set("startDate", v)}
            placeholder="May 2025"
          />
          <Field
            label="End date"
            value={form.endDate}
            onChange={(v) => set("endDate", v)}
            placeholder="Present"
          />
        </div>
      </div>
      <BulletList
        bullets={form.bullets}
        onChange={(b) => set("bullets", b)}
      />
      <ModalFooter
        pending={pending}
        isEdit={!!entry}
        onClose={onClose}
        onSave={save}
      />
    </Modal>
  );
}

function ProjectModal({
  entry,
  onClose,
  run,
}: {
  entry: (ProjectPayload & { id: string }) | null;
  onClose: () => void;
  run: (
    action: () => Promise<MutationResult>,
    success?: string,
  ) => Promise<boolean>;
}) {
  const [form, setForm] = useState<ProjectPayload>({
    name: entry?.name ?? "",
    date: entry?.date ?? "",
    link: entry?.link ?? "",
    bullets: entry?.bullets ?? [],
  });
  const [pending, setPending] = useState(false);

  function set<K extends keyof ProjectPayload>(
    key: K,
    val: ProjectPayload[K],
  ) {
    setForm((f) => ({ ...f, [key]: val }));
  }

  async function save() {
    setPending(true);
    const ok = await run(
      () => (entry ? updateProject(entry.id, form) : addProject(form)),
      entry ? "Project updated" : "Project added",
    );
    setPending(false);
    if (ok) onClose();
  }

  return (
    <Modal
      title={entry ? "Edit project" : "Add project"}
      onClose={onClose}
      maxWidth="max-w-2xl"
    >
      <div className="grid sm:grid-cols-2 gap-3">
        <Field
          label="Project name"
          value={form.name}
          onChange={(v) => set("name", v)}
          placeholder="Apply Tools"
        />
        <Field
          label="Date"
          value={form.date}
          onChange={(v) => set("date", v)}
          placeholder="2026"
        />
        <div className="sm:col-span-2">
          <Field
            label="Link"
            value={form.link}
            onChange={(v) => set("link", v)}
            placeholder="https://github.com/…"
          />
        </div>
      </div>
      <BulletList
        bullets={form.bullets}
        onChange={(b) => set("bullets", b)}
      />
      <ModalFooter
        pending={pending}
        isEdit={!!entry}
        onClose={onClose}
        onSave={save}
      />
    </Modal>
  );
}

function SkillModal({
  entry,
  onClose,
  run,
}: {
  entry: (SkillPayload & { id: string }) | null;
  onClose: () => void;
  run: (
    action: () => Promise<MutationResult>,
    success?: string,
  ) => Promise<boolean>;
}) {
  const [category, setCategory] = useState(entry?.category ?? "");
  const [items, setItems] = useState<string[]>(entry?.items ?? []);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);

  // Add the current input as a chip on Enter or comma. Trims and de-dupes.
  function commitInput() {
    const v = input.trim().replace(/,$/, "").trim();
    if (!v) return;
    if (!items.includes(v)) setItems((xs) => [...xs, v]);
    setInput("");
  }

  async function save() {
    // Fold any un-committed text in the input into the saved items.
    const pendingItem = input.trim().replace(/,$/, "").trim();
    const finalItems =
      pendingItem && !items.includes(pendingItem)
        ? [...items, pendingItem]
        : items;
    setPending(true);
    const ok = await run(
      () =>
        entry
          ? updateSkill(entry.id, { category, items: finalItems })
          : addSkill({ category, items: finalItems }),
      entry ? "Skill category updated" : "Skill category added",
    );
    setPending(false);
    if (ok) onClose();
  }

  return (
    <Modal
      title={entry ? "Edit skill category" : "Add skill category"}
      onClose={onClose}
    >
      <Field
        label="Category"
        value={category}
        onChange={setCategory}
        placeholder="Languages"
      />
      <div className="space-y-2">
        <span className="text-xs font-medium opacity-60">Items</span>
        <div className="flex flex-wrap gap-1.5">
          {items.length === 0 && (
            <span className="text-xs opacity-40">
              Add items below — press Enter to add each.
            </span>
          )}
          {items.map((it, i) => (
            <span
              key={i}
              className="badge badge-ghost gap-1 pr-1"
            >
              {it}
              <button
                type="button"
                onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}
                title="Remove"
                aria-label={`Remove ${it}`}
                className="hover:text-error"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commitInput();
            }
          }}
          placeholder="Python, SQL, R … (Enter to add)"
          className="input input-bordered input-sm w-full"
        />
      </div>
      <ModalFooter
        pending={pending}
        isEdit={!!entry}
        onClose={onClose}
        onSave={save}
      />
    </Modal>
  );
}
