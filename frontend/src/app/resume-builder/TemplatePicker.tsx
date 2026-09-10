"use client";

import { useEffect, useRef, useState } from "react";
import { LayoutTemplate, Loader2, Check } from "lucide-react";
import Modal from "./Modal";
import { DEFAULT_TEMPLATE } from "./types";

// One shell as served by GET /resume-builder/templates.
export type TemplateInfo = {
  slug: string;
  name: string;
  description: string;
};

// Fallback list shown if the backend is unreachable. Deliberately minimal — just
// the default — so the picker still renders (and can't silently offer a slug the
// backend might not have) rather than showing an empty dialog.
const FALLBACK: TemplateInfo[] = [
  {
    slug: DEFAULT_TEMPLATE,
    name: "Classic",
    description: "The default resume layout.",
  },
];

// Cached across mounts: the list is small, static per deploy, and re-fetching it
// every time the dialog opens adds a visible spinner for no reason.
let cache: TemplateInfo[] | null = null;

/**
 * Template chooser for the builder toolbar. Shows the current template's name
 * on the button and opens a dialog listing every shell the backend offers, each
 * with a small preview of what makes it different.
 *
 * Selecting one only updates local state — it's persisted by the editor's normal
 * save, like any other edit, and takes effect on the next Preview/Export render.
 */
export default function TemplatePicker({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (slug: string) => void;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [templates, setTemplates] = useState<TemplateInfo[] | null>(cache);
  const [loading, setLoading] = useState(false);
  // Set on unmount so a fetch still in flight doesn't setState afterwards.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  // Fetched from the open handler rather than an effect: the fetch is a reaction
  // to the click, not synchronisation with external state, and most editing
  // sessions never open the picker at all. The list isn't needed to render the
  // button label, so nothing is fetched on mount.
  async function openPicker() {
    setOpen(true);
    if (templates) return; // already loaded (or served from the module cache)
    setLoading(true);
    try {
      const res = await fetch("/api/proxy/resume-builder/templates");
      if (!res.ok) throw new Error(`templates ${res.status}`);
      const data = await res.json();
      const list: TemplateInfo[] =
        Array.isArray(data?.templates) && data.templates.length
          ? data.templates
          : FALLBACK;
      cache = list;
      if (alive.current) setTemplates(list);
    } catch {
      // Backend down / bad payload — show the default so the dialog isn't empty.
      if (alive.current) setTemplates(FALLBACK);
    } finally {
      if (alive.current) setLoading(false);
    }
  }

  // The label falls back to the raw slug until the list loads, so the button
  // always says something meaningful about the current selection.
  const current = templates?.find((t) => t.slug === value);
  const label = current?.name ?? titleCase(value || DEFAULT_TEMPLATE);

  return (
    <>
      <button
        type="button"
        onClick={openPicker}
        disabled={disabled}
        title="Choose the PDF template"
        className="btn btn-ghost btn-sm gap-1.5"
      >
        <LayoutTemplate className="h-4 w-4" />
        {label}
      </button>

      {open && (
        <Modal
          title="Resume template"
          onClose={() => setOpen(false)}
          maxWidth="max-w-2xl"
        >
          <p className="text-sm opacity-60 -mt-1">
            Changes how the exported PDF looks. Your content stays exactly the
            same — preview to see the result.
          </p>

          {loading && !templates ? (
            <div className="flex items-center gap-2 py-8 justify-center opacity-60">
              <Loader2 className="h-4 w-4 animate-spin" />
              <span className="text-sm">Loading templates…</span>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-2">
              {(templates ?? FALLBACK).map((t) => {
                const selected = t.slug === value;
                return (
                  <button
                    key={t.slug}
                    type="button"
                    onClick={() => {
                      onChange(t.slug);
                      setOpen(false);
                    }}
                    aria-pressed={selected}
                    className={`text-left rounded-lg border p-3 transition-colors ${
                      selected
                        ? "border-primary bg-primary/10"
                        : "border-base-300 hover:border-primary/40"
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <TemplateThumb slug={t.slug} />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="font-medium text-sm">{t.name}</span>
                          {selected && (
                            <Check className="h-3.5 w-3.5 text-primary shrink-0" />
                          )}
                        </div>
                        <p className="text-xs opacity-60 mt-0.5">
                          {t.description}
                        </p>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </Modal>
      )}
    </>
  );
}

// A tiny abstract sketch of each layout — enough to tell the shells apart at a
// glance (rule weight/colour, heading alignment, line density) without shipping
// and maintaining real rendered screenshots. Falls back to the classic sketch
// for any slug added on the backend that this file doesn't know about yet.
function TemplateThumb({ slug }: { slug: string }) {
  const bar = "block rounded-[1px]";
  const line = (w: string, cls = "bg-base-content/25") => (
    <span className={`${bar} ${cls} h-[2px]`} style={{ width: w }} />
  );

  const body = () => {
    switch (slug) {
      case "compact":
        // Classic's layout, but each entry heading is ONE line rather than a
        // stacked pair — sketched as a single split bar (title left, dates right)
        // where classic shows two stacked rows.
        return (
          <>
            <span className={`${bar} bg-base-content/70 h-[3px] w-3/5 self-center`} />
            <span className="flex flex-col gap-[2px] w-full">
              <span className={`${bar} bg-base-content/60 h-[2px] w-1/3`} />
              <span className={`${bar} bg-base-content/40 h-[1px] w-full`} />
              {/* one-line entry heading: left label + right-aligned dates */}
              <span className="flex justify-between w-full">
                <span className={`${bar} bg-base-content/60 h-[2px] w-2/5`} />
                <span className={`${bar} bg-base-content/60 h-[2px] w-1/4`} />
              </span>
              {line("100%")}
              {line("90%")}
              <span className="flex justify-between w-full mt-[1px]">
                <span className={`${bar} bg-base-content/60 h-[2px] w-1/3`} />
                <span className={`${bar} bg-base-content/60 h-[2px] w-1/4`} />
              </span>
              {line("94%")}
            </span>
          </>
        );
      case "modern":
        // Accent-coloured heading + rule.
        return (
          <>
            <span className={`${bar} bg-primary h-[3px] w-3/5 self-center`} />
            <span className="flex flex-col gap-[2px] w-full">
              <span className={`${bar} bg-primary h-[2px] w-1/3`} />
              <span className={`${bar} bg-primary/70 h-[1px] w-full`} />
              {line("100%")}
              {line("90%")}
              <span className={`${bar} bg-primary h-[2px] w-1/4 mt-[2px]`} />
              <span className={`${bar} bg-primary/70 h-[1px] w-full`} />
              {line("94%")}
            </span>
          </>
        );
      case "serif":
        // Centred headings, no rules.
        return (
          <>
            <span className={`${bar} bg-base-content/70 h-[3px] w-3/5 self-center`} />
            <span className="flex flex-col gap-[2px] w-full items-center">
              <span className={`${bar} bg-base-content/60 h-[2px] w-1/3`} />
              {line("100%")}
              {line("90%")}
              <span className={`${bar} bg-base-content/60 h-[2px] w-1/4 mt-[2px]`} />
              {line("94%")}
              {line("86%")}
            </span>
          </>
        );
      default:
        // Classic: left headings over a full-width rule.
        return (
          <>
            <span className={`${bar} bg-base-content/70 h-[3px] w-3/5 self-center`} />
            <span className="flex flex-col gap-[2px] w-full">
              <span className={`${bar} bg-base-content/60 h-[2px] w-1/3`} />
              <span className={`${bar} bg-base-content/40 h-[1px] w-full`} />
              {line("100%")}
              {line("90%")}
              <span className={`${bar} bg-base-content/60 h-[2px] w-1/4 mt-[2px]`} />
              <span className={`${bar} bg-base-content/40 h-[1px] w-full`} />
              {line("94%")}
            </span>
          </>
        );
    }
  };

  return (
    <span
      aria-hidden
      className="shrink-0 w-10 h-[52px] rounded border border-base-300 bg-base-100 p-1 flex flex-col items-start gap-[3px] overflow-hidden"
    >
      {body()}
    </span>
  );
}

function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}
