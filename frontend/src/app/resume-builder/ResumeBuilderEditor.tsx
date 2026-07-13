"use client";

import { createContext, useContext, useEffect, useState, useTransition } from "react";
import { toast } from "sonner";
import {
  Plus,
  Trash2,
  GripVertical,
  Sparkles,
  Highlighter,
  Download,
  Save,
  Wand2,
  Target,
  Gauge,
  Loader2,
  Eye,
  EyeOff,
  Clock,
} from "lucide-react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { saveResumeProfile, deleteResumeProfile, setResumeProfileActive } from "./actions";
import Modal from "./Modal";
import BoldEditor from "./BoldEditor";
import {
  type ResumeProfileData,
  type SkillEntry,
  type ProjectEntry,
  type SectionKey,
  type DateParts,
  SECTION_LABELS,
  MONTHS,
  emptyEducation,
  emptyExperience,
  emptySkill,
  emptyProject,
  normalizeProfile,
  experienceMonths,
  formatDuration,
} from "./types";

// ---- drag-and-drop reordering ----------------------------------------------
//
// The persisted resume shape (types.ts) has no per-entry IDs, and we don't want
// to add any to the saved data. dnd-kit needs stable IDs, so we mint ephemeral
// client-side IDs that live only in component state. `useStableIds` keeps an ID
// array the same length as the data list (appending IDs as items are added,
// dropping them as items are removed) and gives back a `reorder` helper that
// moves an item by old→new index. The parent applies the *same* index move to
// the real data array, so IDs and data stay aligned without touching storage.

let _idSeq = 0;
function nextId(): string {
  _idSeq += 1;
  return `dnd-${_idSeq}`;
}

function useStableIds(count: number): { ids: string[]; reorder: (from: number, to: number) => void } {
  // IDs live in state (not a ref) so reads during render are legitimate. We
  // reconcile length here using React's supported "adjust state during render"
  // pattern: if the data length changed, derive the next ID array immediately
  // and store it before returning. New items get fresh IDs appended; removed
  // items drop trailing IDs. Reorders are applied explicitly via `reorder`.
  const [ids, setIds] = useState<string[]>([]);
  if (ids.length !== count) {
    const next =
      ids.length < count
        ? [...ids, ...Array.from({ length: count - ids.length }, nextId)]
        : ids.slice(0, count);
    setIds(next);
    return { ids: next, reorder };
  }
  function reorder(from: number, to: number) {
    setIds((cur) => arrayMove(cur, from, to));
  }
  return { ids, reorder };
}

// Generic vertical sortable list. `ids` are the stable IDs (one per child);
// `onMove(from, to)` fires after a drag, with array indices. Each child is
// wrapped in a SortableRow that exposes its drag handle via context; the child
// drops a <DragHandle/> wherever the grip should live.
function SortableList<T>({
  id,
  items,
  ids,
  onMove,
  renderItem,
  className,
}: {
  // Stable DndContext id. dnd-kit derives its aria-describedby announcement IDs
  // from this; without a fixed id it uses an auto-incrementing counter that
  // drifts between server and client render, causing a hydration mismatch.
  id: string;
  items: T[];
  ids: string[];
  onMove: (from: number, to: number) => void;
  renderItem: (item: T, index: number) => React.ReactNode;
  className?: string;
}) {
  const sensors = useSensors(
    // A small activation distance so clicks/text-selection inside inputs don't
    // accidentally start a drag.
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  function handleDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    const from = ids.indexOf(String(active.id));
    const to = ids.indexOf(String(over.id));
    if (from === -1 || to === -1) return;
    onMove(from, to);
  }

  return (
    <DndContext id={id} sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        <div className={className}>
          {items.map((item, i) => (
            <SortableRow key={ids[i]} id={ids[i]}>
              {renderItem(item, i)}
            </SortableRow>
          ))}
        </div>
      </SortableContext>
    </DndContext>
  );
}

// The per-row drag handle, surfaced to descendants via context so a row's
// children can place the grip anywhere without prop-drilling dnd-kit's
// attributes/listeners (which also keeps the react-hooks/refs lint happy —
// the spread happens on context values, not on passed-in props).
type RowHandle = {
  setActivatorNodeRef: (el: HTMLElement | null) => void;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  attributes: Record<string, any>;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  listeners: Record<string, any> | undefined;
};

const RowHandleContext = createContext<RowHandle | null>(null);

function SortableRow({ id, children }: { id: string; children: React.ReactNode }) {
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } =
    useSortable({ id });
  const style: React.CSSProperties = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.6 : 1,
    zIndex: isDragging ? 10 : undefined,
    position: "relative",
  };
  return (
    <RowHandleContext.Provider value={{ setActivatorNodeRef, attributes, listeners }}>
      <div ref={setNodeRef} style={style}>
        {children}
      </div>
    </RowHandleContext.Provider>
  );
}

// Grip button. Reads the current row's handle from context and wires itself as
// the drag activator. `className` lets each call site tune size/position.
function DragHandle({ className, label = "Drag to reorder" }: { className?: string; label?: string }) {
  const handle = useContext(RowHandleContext);
  if (!handle) return null;
  // dnd-kit's canonical drag-handle markup: a ref callback (the activator) plus
  // a spread of attributes/listeners on the same element. The new
  // react-hooks/refs rule false-positives on this combo (none of these are ref
  // *value* reads), so it's disabled for the returned element.
  /* eslint-disable react-hooks/refs */
  const activatorRef = handle.setActivatorNodeRef;
  const dragProps = { ...handle.attributes, ...handle.listeners };
  return (
    <button
      type="button"
      ref={activatorRef}
      {...dragProps}
      title={label}
      aria-label={label}
      className={["btn btn-ghost btn-xs btn-square cursor-grab active:cursor-grabbing touch-none", className]
        .filter(Boolean)
        .join(" ")}
    >
      <GripVertical className="h-4 w-4" />
    </button>
  );
}

// ---- small field primitives -------------------------------------------------

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block">
      <span className="text-xs font-medium opacity-60">{label}</span>
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="input input-bordered input-sm w-full mt-1"
      />
    </label>
  );
}

// Structured start/end date editor: month + year selects for start and end,
// plus a "Present" toggle for ongoing entries. Replaces the old free-text Dates
// field; the displayed range on the resume is derived from these via
// formatDateRange, and years-of-experience is computed from them.
const YEAR_NOW = new Date().getFullYear();
const YEARS = Array.from({ length: 60 }, (_, k) => YEAR_NOW + 5 - k); // now+5 → now-54

function MonthYearSelect({
  month,
  year,
  onMonth,
  onYear,
  disabled,
}: {
  month: number | null;
  year: number | null;
  onMonth: (m: number | null) => void;
  onYear: (y: number | null) => void;
  disabled?: boolean;
}) {
  return (
    <div className="flex gap-1 min-w-0">
      <select
        value={month ?? ""}
        disabled={disabled}
        onChange={(e) => onMonth(e.target.value ? Number(e.target.value) : null)}
        className="select select-bordered select-sm flex-1 min-w-0 px-2 disabled:opacity-40"
      >
        <option value="">Mon</option>
        {MONTHS.slice(1).map((m, idx) => (
          <option key={m} value={idx + 1}>{m}</option>
        ))}
      </select>
      <select
        value={year ?? ""}
        disabled={disabled}
        onChange={(e) => onYear(e.target.value ? Number(e.target.value) : null)}
        className="select select-bordered select-sm w-[4.75rem] shrink-0 px-2 disabled:opacity-40"
      >
        <option value="">Year</option>
        {YEARS.map((y) => (
          <option key={y} value={y}>{y}</option>
        ))}
      </select>
    </div>
  );
}

function DateRangeField({
  value,
  onChange,
}: {
  value: DateParts;
  onChange: (patch: Partial<DateParts>) => void;
}) {
  // "From" / "To" act as the field labels (each ABOVE its month+year), so the
  // dropdowns line up with the Location input on the same grid row. The Present
  // toggle floats to the right of the label row.
  // Each date column mirrors <Field> EXACTLY — a `block` label with a `text-xs`
  // span followed by an `mt-1` control — so its line-box metrics match the
  // Location field and the dropdowns land on the same baseline in the grid row.
  // The Present toggle is overlaid at the top-right without affecting layout.
  return (
    <div className="min-w-0 relative">
      <label className="absolute top-0 right-0 flex items-center gap-1.5 text-xs opacity-70 cursor-pointer z-10">
        <input
          type="checkbox"
          checked={value.isPresent}
          onChange={(e) => onChange({ isPresent: e.target.checked })}
          className="checkbox checkbox-xs"
        />
        Present
      </label>
      <div className="flex gap-3 min-w-0">
        <label className="block flex-1 min-w-0">
          <span className="text-xs font-medium opacity-60">From</span>
          <div className="mt-1">
            <MonthYearSelect
              month={value.startMonth}
              year={value.startYear}
              onMonth={(m) => onChange({ startMonth: m })}
              onYear={(y) => onChange({ startYear: y })}
            />
          </div>
        </label>
        <label className="block flex-1 min-w-0">
          <span className="text-xs font-medium opacity-60">To</span>
          <div className="mt-1">
            <MonthYearSelect
              month={value.endMonth}
              year={value.endYear}
              onMonth={(m) => onChange({ endMonth: m })}
              onYear={(y) => onChange({ endYear: y })}
              disabled={value.isPresent}
            />
          </div>
        </label>
      </div>
    </div>
  );
}

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

// AI "improve" + "highlight" wrapper for a single bullet textarea.
function BulletRow({
  value,
  onChange,
  onRemove,
  context,
}: {
  value: string;
  onChange: (v: string) => void;
  onRemove: () => void;
  context: string;
}) {
  // Separate flags so each button shows its own spinner; both share the lock so
  // we never fire two AI calls on the same bullet at once.
  const [improving, setImproving] = useState(false);
  const [highlighting, setHighlighting] = useState(false);
  const busy = improving || highlighting;

  async function improve() {
    if (!value.trim()) {
      toast.error("Write something first, then improve it with AI.");
      return;
    }
    setImproving(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/rewrite-bullet", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: value, context }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Rewrite failed");
      onChange(data.bullet);
      toast.success("Bullet rewritten");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setImproving(false);
    }
  }

  // Bold ~4-5 key words in this bullet without rewording it.
  async function highlight() {
    if (!value.trim()) {
      toast.error("Write something first, then highlight key words.");
      return;
    }
    setHighlighting(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/highlight-bullet", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: value, context }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Highlight failed");
      if (data.drifted) {
        // AI tried to reword the bullet; backend kept the original. Tell the
        // user it didn't apply rather than pretending nothing was notable.
        toast.error("Couldn't highlight cleanly — try again.");
      } else if (data.bullet === value) {
        toast.message("No changes — nothing stood out to highlight.");
      } else {
        onChange(data.bullet);
        toast.success("Key words highlighted");
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setHighlighting(false);
    }
  }

  return (
    <div className="flex items-start gap-2">
      <DragHandle className="mt-7 opacity-30 hover:opacity-70" label="Drag to reorder bullet" />
      <BoldEditor
        value={value}
        onChange={onChange}
        placeholder="Describe an accomplishment — lead with impact and a metric."
      />
      <div className="flex flex-col gap-1 pt-7">
        <button
          type="button"
          onClick={improve}
          disabled={busy}
          title="Improve with AI"
          className="btn btn-ghost btn-xs btn-square text-primary"
        >
          {improving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          type="button"
          onClick={highlight}
          disabled={busy}
          title="Highlight key words (bold ~4-5)"
          className="btn btn-ghost btn-xs btn-square text-primary"
        >
          {highlighting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <Highlighter className="h-3.5 w-3.5" />
          )}
        </button>
        <button
          type="button"
          onClick={onRemove}
          title="Remove bullet"
          className="btn btn-ghost btn-xs btn-square text-error/70"
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  );
}

// ---- main editor ------------------------------------------------------------

export default function ResumeBuilderEditor({
  id,
  initialName,
  initialProfile,
  initialActive,
}: {
  id: string;
  initialName: string;
  initialProfile: ResumeProfileData;
  initialActive: boolean;
}) {
  const [name, setName] = useState(initialName);
  const [profile, setProfile] = useState<ResumeProfileData>(initialProfile);
  const [dirty, setDirty] = useState(false);
  const [active, setActive] = useState(initialActive);
  const [togglingActive, startToggleActive] = useTransition();
  const [saving, startSave] = useTransition();
  const [exporting, setExporting] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  // Page count of the most recent render (preview or export). null = unknown.
  // A resume must fit on one page; export is blocked while this is > 1.
  const [pageCount, setPageCount] = useState<number | null>(null);
  const [tailorOpen, setTailorOpen] = useState(false);
  const [scoreOpen, setScoreOpen] = useState(false);
  const [draftOpen, setDraftOpen] = useState(false);
  const [suggesting, setSuggesting] = useState(false);

  // Stable client-side IDs for the reorderable entry lists (see useStableIds).
  const eduIds = useStableIds(profile.education.length);
  const expIds = useStableIds(profile.experience.length);
  const skillIds = useStableIds(profile.skills.length);
  const projIds = useStableIds(profile.projects.length);
  // Section-level reordering (the whole-section drag list).
  const sectionIds = useStableIds(profile.sectionOrder.length);

  // single mutator that flags dirty state
  function update(mut: (p: ResumeProfileData) => ResumeProfileData) {
    setProfile((p) => mut(structuredClone(p)));
    setDirty(true);
  }

  function setHeader<K extends keyof ResumeProfileData["header"]>(
    key: K,
    val: string,
  ) {
    update((p) => {
      p.header[key] = val;
      return p;
    });
  }

  // Toggle whether this resume appears in the applications / reach-out / AI
  // pickers. Optimistic: flip immediately, revert on failure.
  function toggleActive() {
    if (togglingActive) return;
    const next = !active;
    setActive(next);
    startToggleActive(async () => {
      const res = await setResumeProfileActive(id, next);
      if (res.ok) {
        toast.success(next ? "Active in pickers" : "Hidden from pickers");
      } else {
        setActive(!next);
        toast.error(res.error);
      }
    });
  }

  async function save() {
    return new Promise<boolean>((resolve) => {
      startSave(async () => {
        const res = await saveResumeProfile(id, name, profile);
        if (res.ok) {
          setDirty(false);
          toast.success("Saved");
          resolve(true);
        } else {
          toast.error(res.error);
          resolve(false);
        }
      });
    });
  }

  // Render the current profile to a PDF blob via the backend. Shared by
  // Export (download) and Preview (inline iframe). Also reads the page count
  // the backend reports via X-Page-Count and stashes it in state.
  async function fetchPdfBlob(): Promise<{ blob: Blob; pages: number | null }> {
    const res = await fetch("/api/proxy/resume-builder/pdf", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ profile, filename: name }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.detail || `PDF render failed (${res.status})`);
    }
    const raw = res.headers.get("x-page-count");
    const pages = raw ? Number(raw) : null;
    const resolved = pages && pages > 0 ? pages : null;
    setPageCount(resolved);
    return { blob: await res.blob(), pages: resolved };
  }

  async function exportPdf() {
    setExporting(true);
    try {
      const { blob, pages } = await fetchPdfBlob();
      // Guard: never download a multi-page resume. (The button is already
      // disabled when we know it's >1, but a fresh render could reveal it.)
      if (pages && pages > 1) {
        toast.error(
          `Resume is ${pages} pages — trim it to one page before exporting.`,
        );
        return;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `Resume_${name.replace(/[^A-Za-z0-9._-]+/g, "_")}.pdf`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success("PDF downloaded");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setExporting(false);
    }
  }

  async function preview() {
    setPreviewing(true);
    try {
      const { blob } = await fetchPdfBlob();
      // Revoke any previous preview URL before swapping in the new one.
      setPreviewUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return URL.createObjectURL(blob);
      });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPreviewing(false);
    }
  }

  function closePreview() {
    setPreviewUrl((prev) => {
      if (prev) URL.revokeObjectURL(prev);
      return null;
    });
  }

  // Revoke the object URL on unmount so we don't leak it.
  useEffect(() => {
    return () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    };
  }, [previewUrl]);

  async function suggest() {
    setSuggesting(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/suggest", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ profile }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Suggest failed");
      if (!Array.isArray(data.skills) || !data.skills.length) {
        toast.message("No skill suggestions — add some experience first.");
        return;
      }
      update((p) => {
        p.skills = data.skills.map((s: SkillEntry) => ({
          category: s.category ?? "",
          items: s.items ?? "",
        }));
        return p;
      });
      toast.success("Skills suggested");
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSuggesting(false);
    }
  }

  function applyDraft(raw: unknown) {
    setProfile(normalizeProfile(raw));
    setDirty(true);
    setDraftOpen(false);
    toast.success("Draft loaded — review and edit");
  }

  function applyTailored(raw: unknown, notes: string) {
    setProfile(normalizeProfile(raw));
    setDirty(true);
    setTailorOpen(false);
    toast.success(notes || "Resume tailored to the job description");
  }

  // Reorder helpers: move the data array and the matching ID array in lockstep
  // so dnd-kit's IDs stay aligned with the entries they represent.
  function moveEducation(from: number, to: number) {
    update((p) => ({ ...p, education: arrayMove(p.education, from, to) }));
    eduIds.reorder(from, to);
  }
  function moveExperience(from: number, to: number) {
    update((p) => ({ ...p, experience: arrayMove(p.experience, from, to) }));
    expIds.reorder(from, to);
  }
  function moveSkill(from: number, to: number) {
    update((p) => ({ ...p, skills: arrayMove(p.skills, from, to) }));
    skillIds.reorder(from, to);
  }
  function moveProject(from: number, to: number) {
    update((p) => ({ ...p, projects: arrayMove(p.projects, from, to) }));
    projIds.reorder(from, to);
  }
  // Reorder whole sections, and toggle a section's visibility. These drive both
  // the editor layout and the rendered PDF / plaintext output.
  function moveSection(from: number, to: number) {
    update((p) => ({ ...p, sectionOrder: arrayMove(p.sectionOrder, from, to) }));
    sectionIds.reorder(from, to);
  }
  function toggleSection(key: SectionKey) {
    update((p) => ({
      ...p,
      sectionOrder: p.sectionOrder.map((s) =>
        s.key === key ? { ...s, visible: !s.visible } : s,
      ),
    }));
  }
  function setSummary(val: string) {
    update((p) => ({ ...p, summary: val }));
  }

  // Render one section's editor body by key. Kept as hand-written blocks (each
  // section has its own fields/DnD); the section ORDER and visibility are what's
  // now dynamic. Header renders separately, fixed at the top.
  function renderSectionBody(key: SectionKey) {
    switch (key) {
      case "summary":
        return (
          <SectionCard title="Summary">
            <textarea
              value={profile.summary}
              onChange={(e) => setSummary(e.target.value)}
              rows={4}
              placeholder="A short professional summary (2–4 sentences)."
              className="textarea textarea-bordered textarea-sm w-full leading-relaxed"
            />
          </SectionCard>
        );
      case "education":
        return (
          <SectionCard
            title="Education"
            action={
              <button type="button" onClick={() => update((p) => ({ ...p, education: [...p.education, emptyEducation()] }))} className="btn btn-ghost btn-xs gap-1">
                <Plus className="h-3.5 w-3.5" /> Add
              </button>
            }
          >
            {profile.education.length === 0 && <Empty label="No education entries yet." />}
            <SortableList
              id="dnd-education"
              items={profile.education}
              ids={eduIds.ids}
              onMove={moveEducation}
              className="space-y-4"
              renderItem={(e, i) => (
                <EntryFrame
                  onRemove={() => update((p) => ({ ...p, education: p.education.filter((_, j) => j !== i) }))}
                >
                  <div className="grid sm:grid-cols-2 gap-3">
                    <Field label="School" value={e.school} onChange={(v) => editEdu(update, i, "school", v)} />
                    <Field label="Degree" value={e.degree} onChange={(v) => editEdu(update, i, "degree", v)} />
                    <Field label="Location" value={e.location} onChange={(v) => editEdu(update, i, "location", v)} />
                    <DateRangeField value={e} onChange={(patch) => editEduDates(update, i, patch)} />
                  </div>
                </EntryFrame>
              )}
            />
          </SectionCard>
        );
      case "experience": {
        const totalExp = formatDuration(experienceMonths(profile.experience));
        return (
          <SectionCard
            title="Professional Experience"
            action={
              <div className="flex items-center gap-2">
                {totalExp && (
                  <span
                    title="Total experience, merging overlapping periods (‘Present’ counts to this month)"
                    className="badge badge-outline badge-sm gap-1 font-medium tabular-nums"
                  >
                    <Clock className="h-3 w-3" />
                    {totalExp}
                  </span>
                )}
                <button type="button" onClick={() => update((p) => ({ ...p, experience: [...p.experience, emptyExperience()] }))} className="btn btn-ghost btn-xs gap-1">
                  <Plus className="h-3.5 w-3.5" /> Add
                </button>
              </div>
            }
          >
            {profile.experience.length === 0 && <Empty label="No experience entries yet." />}
            <SortableList
              id="dnd-experience"
              items={profile.experience}
              ids={expIds.ids}
              onMove={moveExperience}
              className="space-y-4"
              renderItem={(x, i) => (
                <EntryFrame
                  onRemove={() => update((p) => ({ ...p, experience: p.experience.filter((_, j) => j !== i) }))}
                >
                  <div className="grid sm:grid-cols-2 gap-3">
                    <Field label="Company" value={x.company} onChange={(v) => editExp(update, i, "company", v)} />
                    <Field label="Title" value={x.title} onChange={(v) => editExp(update, i, "title", v)} />
                    <Field label="Location" value={x.location} onChange={(v) => editExp(update, i, "location", v)} />
                    <DateRangeField value={x} onChange={(patch) => editExpDates(update, i, patch)} />
                  </div>
                  <BulletEditor
                    bullets={x.bullets}
                    context={`${x.title} at ${x.company}`}
                    listId={`experience-${i}`}
                    onChange={(bullets) => update((p) => { p.experience[i].bullets = bullets; return p; })}
                  />
                </EntryFrame>
              )}
            />
          </SectionCard>
        );
      }
      case "skills":
        return (
          <SectionCard
            title="Technical Skills"
            action={
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={suggest}
                  disabled={suggesting}
                  className="btn btn-ghost btn-xs gap-1.5 text-primary"
                >
                  {suggesting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />}
                  Suggest
                </button>
                <button type="button" onClick={() => update((p) => ({ ...p, skills: [...p.skills, emptySkill()] }))} className="btn btn-ghost btn-xs gap-1">
                  <Plus className="h-3.5 w-3.5" /> Add category
                </button>
              </div>
            }
          >
            {profile.skills.length === 0 && <Empty label="No skill categories yet." />}
            <SortableList
              id="dnd-skills"
              items={profile.skills}
              ids={skillIds.ids}
              onMove={moveSkill}
              className="space-y-3"
              renderItem={(s, i) => (
                <div className="flex items-center gap-2">
                  <DragHandle className="opacity-30 hover:opacity-70" label="Drag to reorder category" />
                  <input
                    value={s.category}
                    onChange={(e) => editSkill(update, i, "category", e.target.value)}
                    placeholder="Category (e.g. Languages)"
                    className="input input-bordered input-sm w-48 shrink-0"
                  />
                  <input
                    value={s.items}
                    onChange={(e) => editSkill(update, i, "items", e.target.value)}
                    placeholder="Python, SQL, R, …"
                    className="input input-bordered input-sm flex-1"
                  />
                  <button type="button" onClick={() => update((p) => ({ ...p, skills: p.skills.filter((_, j) => j !== i) }))} className="btn btn-ghost btn-xs btn-square text-error/70">
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}
            />
          </SectionCard>
        );
      case "projects":
        return (
          <SectionCard
            title="Projects"
            action={
              <button type="button" onClick={() => update((p) => ({ ...p, projects: [...p.projects, emptyProject()] }))} className="btn btn-ghost btn-xs gap-1">
                <Plus className="h-3.5 w-3.5" /> Add
              </button>
            }
          >
            {profile.projects.length === 0 && <Empty label="No projects yet." />}
            <SortableList
              id="dnd-projects"
              items={profile.projects}
              ids={projIds.ids}
              onMove={moveProject}
              className="space-y-4"
              renderItem={(pr, i) => (
                <EntryFrame
                  onRemove={() => update((p) => ({ ...p, projects: p.projects.filter((_, j) => j !== i) }))}
                >
                  <div className="grid sm:grid-cols-2 gap-3">
                    <Field label="Project name" value={pr.name} onChange={(v) => editProj(update, i, "name", v)} />
                    <Field label="Date" value={pr.date} onChange={(v) => editProj(update, i, "date", v)} placeholder="2026" />
                  </div>
                  <BulletEditor
                    bullets={pr.bullets}
                    context={`Project: ${pr.name}`}
                    listId={`project-${i}`}
                    onChange={(bullets) => update((p) => { p.projects[i].bullets = bullets; return p; })}
                  />
                </EntryFrame>
              )}
            />
          </SectionCard>
        );
      default:
        return null;
    }
  }

  // A resume must fit one page. Once we know the count (after a preview/export
  // render), a >1 result blocks export until the user trims it down.
  const overflow = pageCount !== null && pageCount > 1;

  return (
    <div className="space-y-5">
      {/* Sticky toolbar */}
      <div className="sticky top-0 z-20 -mx-1 px-1 py-2 bg-base-100 border-b border-base-300 flex flex-wrap items-center gap-2">
        <input
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setDirty(true);
          }}
          className="input input-bordered input-sm font-medium flex-1 min-w-[200px]"
          placeholder="Resume name"
        />
        <button
          type="button"
          onClick={() => setDraftOpen(true)}
          className="btn btn-ghost btn-sm gap-1.5"
        >
          <Wand2 className="h-4 w-4" /> Draft from notes
        </button>
        <button
          type="button"
          onClick={() => setTailorOpen(true)}
          className="btn btn-ghost btn-sm gap-1.5"
        >
          <Target className="h-4 w-4" /> Tailor to JD
        </button>
        <button
          type="button"
          onClick={() => setScoreOpen(true)}
          className="btn btn-ghost btn-sm gap-1.5"
        >
          <Gauge className="h-4 w-4" /> Score role
        </button>
        <button
          type="button"
          onClick={preview}
          disabled={previewing}
          className="btn btn-ghost btn-sm gap-1.5"
        >
          {previewing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Eye className="h-4 w-4" />
          )}
          Preview
        </button>
        <button
          type="button"
          onClick={toggleActive}
          disabled={togglingActive}
          title={
            active
              ? "This resume is selectable in the applications, reach-out, and AI pickers. Click to hide it."
              : "This resume is hidden from the pickers. Click to make it selectable."
          }
          className={`btn btn-sm gap-1.5 ${active ? "btn-success btn-outline" : "btn-ghost"}`}
        >
          {togglingActive ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : active ? (
            <Eye className="h-4 w-4" />
          ) : (
            <EyeOff className="h-4 w-4" />
          )}
          {active ? "Active" : "Inactive"}
        </button>
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
        {pageCount !== null && <PageBadge pages={pageCount} />}
        <button
          type="button"
          onClick={async () => {
            if (dirty) await save();
            await exportPdf();
          }}
          disabled={exporting || overflow}
          title={
            overflow
              ? "Trim your resume to one page before exporting"
              : undefined
          }
          className="btn btn-gradient btn-sm gap-1.5"
        >
          {exporting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
          Export PDF
        </button>
      </div>

      {/* Header / contact */}
      <SectionCard title="Header">
        <div className="grid sm:grid-cols-2 gap-3">
          <Field label="Full name" value={profile.header.fullName} onChange={(v) => setHeader("fullName", v)} placeholder="Amogh Ramagiri" />
          <Field label="Location" value={profile.header.location} onChange={(v) => setHeader("location", v)} placeholder="Arlington, VA" />
          <Field label="Phone" value={profile.header.phone} onChange={(v) => setHeader("phone", v)} placeholder="571-478-2290" />
          <Field label="Email" value={profile.header.email} onChange={(v) => setHeader("email", v)} placeholder="you@email.com" />
          <Field label="LinkedIn URL" value={profile.header.linkedin} onChange={(v) => setHeader("linkedin", v)} placeholder="https://linkedin.com/in/…" />
          <Field label="GitHub URL" value={profile.header.github} onChange={(v) => setHeader("github", v)} placeholder="https://github.com/…" />
          <Field label="Portfolio URL" value={profile.header.portfolio} onChange={(v) => setHeader("portfolio", v)} placeholder="https://…" />
          <Field label="Publications URL" value={profile.header.scholar} onChange={(v) => setHeader("scholar", v)} placeholder="Google Scholar…" />
        </div>
      </SectionCard>

      {/* Sections manager — reorder + show/hide whole sections. Drives both the
          editor layout below and the rendered PDF / plaintext output. */}
      <SectionCard title="Sections">
        <p className="text-xs opacity-50 mb-3">
          Drag to reorder. Toggle a section off to hide it from the resume (its
          content is kept).
        </p>
        <SortableList
          id="dnd-sections"
          items={profile.sectionOrder}
          ids={sectionIds.ids}
          onMove={moveSection}
          className="space-y-1.5"
          renderItem={(s) => (
            <div className="flex items-center gap-3 rounded-lg border border-base-300 bg-base-100/40 px-3 py-2">
              <DragHandle className="opacity-30 hover:opacity-70" label="Drag to reorder section" />
              <span className={`text-sm font-medium flex-1 ${s.visible ? "" : "opacity-40"}`}>
                {SECTION_LABELS[s.key]}
              </span>
              <button
                type="button"
                onClick={() => toggleSection(s.key)}
                title={s.visible ? "Hide from resume" : "Show on resume"}
                aria-label={s.visible ? `Hide ${SECTION_LABELS[s.key]}` : `Show ${SECTION_LABELS[s.key]}`}
                className={`btn btn-ghost btn-xs gap-1.5 ${s.visible ? "text-primary" : "opacity-50"}`}
              >
                {s.visible ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                {s.visible ? "Shown" : "Hidden"}
              </button>
            </div>
          )}
        />
      </SectionCard>

      {/* Section bodies, rendered in the profile's order; hidden sections are
          dimmed but still editable. */}
      {profile.sectionOrder.map((s) => (
        <div key={s.key} className={s.visible ? "" : "opacity-50"}>
          {renderSectionBody(s.key)}
        </div>
      ))}

      {/* Danger zone */}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={() => {
            if (confirm("Delete this resume permanently?")) deleteResumeProfile(id);
          }}
          className="btn btn-ghost btn-sm text-error/80 gap-1.5"
        >
          <Trash2 className="h-4 w-4" /> Delete resume
        </button>
      </div>

      {draftOpen && <DraftModal onClose={() => setDraftOpen(false)} onApply={applyDraft} />}
      {tailorOpen && (
        <TailorModal profile={profile} onClose={() => setTailorOpen(false)} onApply={applyTailored} />
      )}
      {scoreOpen && (
        <ScoreModal profile={profile} onClose={() => setScoreOpen(false)} />
      )}
      {previewUrl && (
        <PreviewModal
          url={previewUrl}
          name={name}
          pages={pageCount}
          onClose={closePreview}
          onExport={async () => {
            if (dirty) await save();
            await exportPdf();
          }}
          exporting={exporting}
        />
      )}
    </div>
  );
}

// A small page-count chip: green when the resume fits one page, red when it
// spills over. Mono so the count reads as data.
function PageBadge({ pages }: { pages: number }) {
  const ok = pages <= 1;
  return (
    <span
      className={[
        "inline-flex items-center gap-1 px-2 py-1 rounded-md border text-xs font-mono whitespace-nowrap",
        ok
          ? "bg-success/12 text-success border-success/25"
          : "bg-error/12 text-error border-error/25",
      ].join(" ")}
      title={ok ? undefined : "Re-arrange or trim bullets to fit one page"}
    >
      {ok ? "1 page" : `${pages} pages`}
    </span>
  );
}

// Inline PDF preview rendered in a large portaled modal (reuses Modal's
// body-portal + Escape handling). The PDF is shown in an iframe pointed at
// the object URL of the rendered blob.
function PreviewModal({
  url,
  name,
  pages,
  onClose,
  onExport,
  exporting,
}: {
  url: string;
  name: string;
  pages: number | null;
  onClose: () => void;
  onExport: () => void;
  exporting: boolean;
}) {
  const overflow = pages !== null && pages > 1;
  return (
    <Modal title="" onClose={onClose} maxWidth="max-w-4xl">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-lg font-semibold truncate">
            Preview — {name || "Resume"}
          </h2>
          {pages !== null && <PageBadge pages={pages} />}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onExport}
            disabled={exporting || overflow}
            title={
              overflow
                ? "Trim your resume to one page before exporting"
                : undefined
            }
            className="btn btn-gradient btn-sm gap-1.5"
          >
            {exporting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
            Export PDF
          </button>
          <button type="button" onClick={onClose} className="btn btn-ghost btn-sm">
            Close
          </button>
        </div>
      </div>
      <iframe
        src={url}
        title="Resume preview"
        className="w-full rounded-lg border border-base-300 bg-base-200"
        style={{ height: "75vh" }}
      />
    </Modal>
  );
}

// Per-bullet list with add button. `listId` is the parent entry's stable id,
// used to give this bullet DndContext a deterministic id (avoids SSR hydration
// drift in dnd-kit's announcement IDs).
function BulletEditor({
  bullets,
  context,
  onChange,
  listId,
}: {
  bullets: string[];
  context: string;
  onChange: (b: string[]) => void;
  listId: string;
}) {
  const { ids, reorder } = useStableIds(bullets.length);

  function move(from: number, to: number) {
    onChange(arrayMove(bullets, from, to));
    reorder(from, to);
  }

  return (
    <div className="space-y-2">
      <span className="text-xs font-medium opacity-60">Bullets</span>
      <SortableList
        id={`dnd-bullets-${listId}`}
        items={bullets}
        ids={ids}
        onMove={move}
        className="space-y-2"
        renderItem={(b, i) => (
          <BulletRow
            value={b}
            context={context}
            onChange={(v) => onChange(bullets.map((x, j) => (j === i ? v : x)))}
            onRemove={() => onChange(bullets.filter((_, j) => j !== i))}
          />
        )}
      />
      <button type="button" onClick={() => onChange([...bullets, ""])} className="btn btn-ghost btn-xs gap-1">
        <Plus className="h-3 w-3" /> Add bullet
      </button>
    </div>
  );
}

function EntryFrame({ children, onRemove }: { children: React.ReactNode; onRemove: () => void }) {
  return (
    <div className="rounded-xl border border-base-300 bg-base-200/30 p-4 relative">
      <div className="absolute top-2.5 right-2.5 flex items-center gap-1">
        <DragHandle className="opacity-40 hover:opacity-80" />
        <button type="button" onClick={onRemove} className="btn btn-ghost btn-xs btn-square text-error/70 hover:bg-error/10">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

function Empty({ label }: { label: string }) {
  return <p className="text-sm opacity-40 py-2">{label}</p>;
}

// --- AI modals ---------------------------------------------------------------

function DraftModal({ onClose, onApply }: { onClose: () => void; onApply: (raw: unknown) => void }) {
  const [notes, setNotes] = useState("");
  const [pending, setPending] = useState(false);

  async function run() {
    if (!notes.trim()) return;
    setPending(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/draft", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ notes }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Draft failed");
      onApply(data.profile);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal title="Draft from notes" onClose={onClose}>
      <p className="text-xs opacity-60">
        Paste an old resume, rough notes, or a brain-dump. AI will extract
        structured education, experience, skills, and projects. This replaces
        the current fields — review before saving.
      </p>
      <textarea
        autoFocus
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        rows={10}
        placeholder="Paste anything…"
        className="textarea textarea-bordered w-full resize-y text-sm"
      />
      <ModalActions pending={pending} onClose={onClose} onRun={run} runLabel="Generate draft" />
    </Modal>
  );
}

function TailorModal({
  profile,
  onClose,
  onApply,
}: {
  profile: ResumeProfileData;
  onClose: () => void;
  onApply: (raw: unknown, notes: string) => void;
}) {
  const [jd, setJd] = useState("");
  const [company, setCompany] = useState("");
  const [pending, setPending] = useState(false);

  async function run() {
    if (!jd.trim()) return;
    setPending(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/tailor", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ profile, job_description: jd, company: company || null }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Tailor failed");
      onApply(data.profile, data.notes);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  return (
    <Modal title="Tailor to a job description" onClose={onClose}>
      <p className="text-xs opacity-60">
        Reorders and rewrites your existing bullets and skills to match the
        role. Never invents experience. Replaces the current fields — review
        before saving.
      </p>
      <input
        value={company}
        onChange={(e) => setCompany(e.target.value)}
        placeholder="Company (optional)"
        className="input input-bordered input-sm w-full"
      />
      <textarea
        autoFocus
        value={jd}
        onChange={(e) => setJd(e.target.value)}
        rows={9}
        placeholder="Paste the job description…"
        className="textarea textarea-bordered w-full resize-y text-sm"
      />
      <ModalActions pending={pending} onClose={onClose} onRun={run} runLabel="Tailor resume" />
    </Modal>
  );
}

// Score the resume the user is editing against a pasted role / job description.
// Read-only: it shows a fit score + verdict + breakdown, never mutates the
// profile (unlike Tailor). Uses the shared backend scorer via /resume-builder/score.
type ScoreResult = {
  score: number;
  score_100: number;
  verdict: string;
  breakdown: {
    must_haves_total: number;
    must_haves_yes: number;
    must_haves_partial: number;
    important_total: number;
    important_yes: number;
    important_partial: number;
    nice_total: number;
    nice_yes: number;
    nice_partial: number;
    missing_must_haves: string[];
  };
  // Present only when scored from a role title (vs a pasted JD).
  generated_jd?: string;
  role_title?: string;
};

function scoreTone(score10: number): string {
  if (score10 >= 8) return "text-success";
  if (score10 >= 6) return "text-warning";
  return "text-error";
}

function ScoreModal({
  profile,
  onClose,
}: {
  profile: ResumeProfileData;
  onClose: () => void;
}) {
  const [role, setRole] = useState("");
  const [jd, setJd] = useState("");
  const [company, setCompany] = useState("");
  const [pending, setPending] = useState(false);
  const [result, setResult] = useState<ScoreResult | null>(null);

  // Either a role title or a pasted JD is enough to score.
  const canScore = role.trim().length > 0 || jd.trim().length > 0;

  async function run() {
    if (!canScore) return;
    setPending(true);
    setResult(null);
    try {
      const res = await fetch("/api/proxy/resume-builder/score", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          profile,
          // JD wins if pasted; otherwise the backend generates one from the role.
          job_description: jd.trim() || null,
          role: jd.trim() ? null : role.trim() || null,
          company: company || null,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || "Scoring failed");
      setResult(data as ScoreResult);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setPending(false);
    }
  }

  // Verdict from the backend is "X/10 - summary"; strip the leading score since
  // we render the number prominently ourselves.
  const summary = result
    ? result.verdict.replace(/^\s*\d+\s*\/\s*10\s*[-:]\s*/, "")
    : "";

  return (
    <Modal title="Score against a role" onClose={onClose} maxWidth="max-w-2xl">
      <p className="text-xs opacity-60">
        Grades the resume you&apos;re editing (including unsaved changes). Enter a
        role and the AI builds typical requirements for it — or paste a full job
        description. Read-only; it won&apos;t change your resume.
      </p>
      <div className="grid sm:grid-cols-2 gap-2">
        <input
          autoFocus
          value={role}
          onChange={(e) => setRole(e.target.value)}
          placeholder="Role, e.g. Senior ML Engineer"
          className="input input-bordered input-sm w-full"
        />
        <input
          value={company}
          onChange={(e) => setCompany(e.target.value)}
          placeholder="Company (optional)"
          className="input input-bordered input-sm w-full"
        />
      </div>
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider opacity-40">
        <span className="h-px flex-1 bg-base-300" />
        or paste a full JD
        <span className="h-px flex-1 bg-base-300" />
      </div>
      <textarea
        value={jd}
        onChange={(e) => setJd(e.target.value)}
        rows={result ? 3 : 7}
        placeholder="Optional — paste a job description to score against it instead of the role…"
        className="textarea textarea-bordered w-full resize-y text-sm"
      />

      {result && (
        <div className="rounded-lg border border-base-300 bg-base-200/40 p-4 space-y-3 animate-fade-in">
          {result.role_title && (
            <div className="text-[11px] uppercase tracking-wider opacity-50 font-medium">
              Scored against generated requirements for{" "}
              <span className="text-base-content normal-case font-semibold">
                {result.role_title}
              </span>
            </div>
          )}
          <div className="flex items-center gap-4">
            <div className={`text-4xl font-bold tabular-nums ${scoreTone(result.score)}`}>
              {result.score}
              <span className="text-lg opacity-50 font-medium">/10</span>
            </div>
            <p className="text-sm leading-snug flex-1">{summary}</p>
          </div>

          <div className="flex flex-wrap gap-2 text-xs">
            <TierChip
              label="Must-haves"
              yes={result.breakdown.must_haves_yes}
              partial={result.breakdown.must_haves_partial}
              total={result.breakdown.must_haves_total}
            />
            <TierChip
              label="Important"
              yes={result.breakdown.important_yes}
              partial={result.breakdown.important_partial}
              total={result.breakdown.important_total}
            />
            <TierChip
              label="Nice-to-have"
              yes={result.breakdown.nice_yes}
              partial={result.breakdown.nice_partial}
              total={result.breakdown.nice_total}
            />
          </div>

          {result.breakdown.missing_must_haves.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wider opacity-50 font-medium mb-1.5">
                Missing must-haves
              </div>
              <ul className="space-y-1">
                {result.breakdown.missing_must_haves.map((m, i) => (
                  <li key={i} className="text-sm flex items-start gap-1.5 text-error">
                    <span className="opacity-70">✕</span>
                    <span className="text-base-content">{m}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={pending}>
          {result ? "Close" : "Cancel"}
        </button>
        <button
          type="button"
          className="btn btn-gradient btn-sm gap-1.5"
          onClick={run}
          disabled={pending || !canScore}
        >
          {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Gauge className="h-4 w-4" />}
          {pending ? "Scoring…" : result ? "Re-score" : "Score fit"}
        </button>
      </div>
    </Modal>
  );
}

// "yes/total" coverage chip; counts a partial as half (shown as +N).
function TierChip({
  label,
  yes,
  partial,
  total,
}: {
  label: string;
  yes: number;
  partial: number;
  total: number;
}) {
  if (total === 0) return null;
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md border border-base-300 bg-base-100 px-2 py-1">
      <span className="opacity-60">{label}</span>
      <span className="font-mono font-medium tabular-nums">
        {yes}/{total}
        {partial > 0 && <span className="opacity-50"> (+{partial})</span>}
      </span>
    </span>
  );
}

function ModalActions({
  pending,
  onClose,
  onRun,
  runLabel,
}: {
  pending: boolean;
  onClose: () => void;
  onRun: () => void;
  runLabel: string;
}) {
  return (
    <div className="flex justify-end gap-2">
      <button type="button" className="btn btn-ghost btn-sm" onClick={onClose} disabled={pending}>
        Cancel
      </button>
      <button type="button" className="btn btn-gradient btn-sm gap-1.5" onClick={onRun} disabled={pending}>
        {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Sparkles className="h-4 w-4" />}
        {runLabel}
      </button>
    </div>
  );
}

// --- field-edit helpers (typed, keep mutation logic out of JSX) --------------

type Updater = (mut: (p: ResumeProfileData) => ResumeProfileData) => void;

// String-only edit helpers (school/degree/location, company/title/location).
// Date fields are structured and edited via editEduDates/editExpDates below.
type EduStrKey = "school" | "degree" | "location";
type ExpStrKey = "company" | "title" | "location";
function editEdu(update: Updater, i: number, key: EduStrKey, v: string) {
  update((p) => { p.education[i][key] = v; return p; });
}
function editExp(update: Updater, i: number, key: ExpStrKey, v: string) {
  update((p) => { p.experience[i][key] = v; return p; });
}
// Merge structured date changes into an entry.
function editEduDates(update: Updater, i: number, patch: Partial<DateParts>) {
  update((p) => { p.education[i] = { ...p.education[i], ...patch }; return p; });
}
function editExpDates(update: Updater, i: number, patch: Partial<DateParts>) {
  update((p) => { p.experience[i] = { ...p.experience[i], ...patch }; return p; });
}
function editSkill(update: Updater, i: number, key: keyof SkillEntry, v: string) {
  update((p) => { p.skills[i][key] = v; return p; });
}
function editProj(update: Updater, i: number, key: keyof Omit<ProjectEntry, "bullets">, v: string) {
  update((p) => { p.projects[i][key] = v; return p; });
}
