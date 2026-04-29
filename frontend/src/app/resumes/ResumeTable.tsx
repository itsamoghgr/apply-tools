"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { bulkSetActive } from "./actions";

type Row = {
  id: string;
  label: string;
  isActive: boolean;
  updatedAt: string;
};

type SortKey =
  | "active-name-asc"
  | "active-name-desc"
  | "name-asc"
  | "name-desc"
  | "recent";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "active-name-asc", label: "Active first · A → Z" },
  { value: "active-name-desc", label: "Active first · Z → A" },
  { value: "name-asc", label: "Name · A → Z" },
  { value: "name-desc", label: "Name · Z → A" },
  { value: "recent", label: "Recently updated" },
];

function sortResumes(rows: Row[], key: SortKey): Row[] {
  const cmpName = (a: Row, b: Row) =>
    a.label.localeCompare(b.label, undefined, { sensitivity: "base" });
  const sorted = [...rows];
  switch (key) {
    case "active-name-asc":
      sorted.sort(
        (a, b) => Number(b.isActive) - Number(a.isActive) || cmpName(a, b),
      );
      break;
    case "active-name-desc":
      sorted.sort(
        (a, b) => Number(b.isActive) - Number(a.isActive) || -cmpName(a, b),
      );
      break;
    case "name-asc":
      sorted.sort(cmpName);
      break;
    case "name-desc":
      sorted.sort((a, b) => -cmpName(a, b));
      break;
    case "recent":
      sorted.sort(
        (a, b) => +new Date(b.updatedAt) - +new Date(a.updatedAt),
      );
      break;
  }
  return sorted;
}

export default function ResumeTable({ resumes }: { resumes: Row[] }) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sortKey, setSortKey] = useState<SortKey>("active-name-asc");

  const sortedResumes = useMemo(
    () => sortResumes(resumes, sortKey),
    [resumes, sortKey],
  );

  const allSelected =
    sortedResumes.length > 0 && selected.size === sortedResumes.length;
  const someSelected = selected.size > 0 && !allSelected;

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected((prev) =>
      prev.size === sortedResumes.length
        ? new Set()
        : new Set(sortedResumes.map((r) => r.id)),
    );
  }

  async function applyBulk(isActive: boolean) {
    if (selected.size === 0 || busy) return;
    setBusy(true);
    const verb = isActive ? "activated" : "deactivated";
    try {
      const ids = Array.from(selected);
      const result = await bulkSetActive(ids, isActive);
      if (result.error) throw new Error(result.error);
      toast.success(
        `${verb} ${result.updated} resume${result.updated === 1 ? "" : "s"}`,
      );
      setSelected(new Set());
      startTransition(() => router.refresh());
    } catch (e) {
      toast.error(`Bulk ${verb.slice(0, -1)} failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  const selectionSummary = useMemo(() => {
    if (selected.size === 0) return "";
    const sel = resumes.filter((r) => selected.has(r.id));
    const active = sel.filter((r) => r.isActive).length;
    const inactive = sel.length - active;
    return `${sel.length} selected · ${active} active, ${inactive} inactive`;
  }, [selected, resumes]);

  const activeCount = useMemo(
    () => resumes.filter((r) => r.isActive).length,
    [resumes],
  );

  return (
    <div className="space-y-3">
      {/* ─── Bulk action bar ─── */}
      {selected.size > 0 && (
        <div className="glass-card px-4 py-3 flex items-center justify-between animate-fade-in">
          <span className="text-sm opacity-80">{selectionSummary}</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => applyBulk(true)}
              className="btn btn-success btn-sm"
            >
              Activate
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => applyBulk(false)}
              className="btn btn-neutral btn-sm"
            >
              Deactivate
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setSelected(new Set())}
              className="btn btn-ghost btn-sm"
            >
              Clear
            </button>
          </div>
        </div>
      )}

      {/* ─── Sort + stats row ─── */}
      <div className="flex items-center justify-between gap-3 text-xs">
        <div className="opacity-60">
          {resumes.length} resume{resumes.length === 1 ? "" : "s"} ·{" "}
          <span className="text-success">{activeCount} active</span>
          {resumes.length - activeCount > 0 && (
            <>
              ,{" "}
              <span className="opacity-50">
                {resumes.length - activeCount} inactive
              </span>
            </>
          )}
        </div>
        <label className="flex items-center gap-2 opacity-60">
          <span className="uppercase tracking-widest text-[10px]">Sort</span>
          <select
            value={sortKey}
            onChange={(e) => setSortKey(e.target.value as SortKey)}
            className="select select-bordered select-xs"
          >
            {SORT_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* ─── Table ─── */}
      <div className="glass-card overflow-hidden">
        <table className="table table-sm">
          <thead>
            <tr className="border-b border-base-300/40">
              <th className="w-10">
                <input
                  type="checkbox"
                  aria-label="Select all"
                  checked={allSelected}
                  ref={(el) => {
                    if (el) el.indeterminate = someSelected;
                  }}
                  onChange={toggleAll}
                  className="checkbox checkbox-primary checkbox-sm"
                />
              </th>
              <th>ID</th>
              <th>Label</th>
              <th>Active</th>
              <th>Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {sortedResumes.map((r) => {
              const checked = selected.has(r.id);
              return (
                <tr
                  key={r.id}
                  className={`transition-colors ${checked ? "bg-primary/5" : "hover:bg-base-200/40"}`}
                >
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${r.label}`}
                      checked={checked}
                      onChange={() => toggle(r.id)}
                      className="checkbox checkbox-primary checkbox-sm"
                    />
                  </td>
                  <td className="font-mono text-xs opacity-50">{r.id}</td>
                  <td className="font-medium">{r.label}</td>
                  <td>
                    {r.isActive ? (
                      <span className="inline-flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-success shadow-[0_0_6px] shadow-success/50" />
                        <span className="text-xs text-success font-medium">active</span>
                      </span>
                    ) : (
                      <span className="badge badge-ghost badge-sm opacity-50">inactive</span>
                    )}
                  </td>
                  <td className="opacity-50 text-xs tabular-nums">
                    {new Date(r.updatedAt).toLocaleString()}
                  </td>
                  <td className="text-right">
                    <Link
                      href={`/resumes/${r.id}`}
                      className="text-sm text-primary hover:text-accent transition-colors"
                    >
                      Edit →
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
