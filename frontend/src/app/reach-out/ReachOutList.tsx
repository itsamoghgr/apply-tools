"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Trash2,
  ChevronDown,
  ChevronUp,
  Send,
  Eye,
  MousePointerClick,
  Pencil,
} from "lucide-react";

type Item = {
  id: string;
  recipientName: string;
  recipientEmail: string;
  subject: string;
  body: string;
  status: string;
  sentAt: string | null;
  errorMessage: string | null;
  openCount: number;
  clickCount: number;
  lastOpenedAt: string | null;
  lastClickedAt: string | null;
  createdAt: string;
};

type ReachOutEvent = {
  id: string;
  reachOutId: string;
  eventType: "open" | "click" | string;
  trackedUrl: string | null;
  userAgent: string | null;
  userIp: string | null;
  createdAt: string;
};

type Props = {
  initial: Item[];
};

// Subtle inline status pill. We avoid DaisyUI's `.badge` class because at
// small sizes it renders with chunky vertical padding that visually
// dominates the row. Using a thin border + tinted background reads as
// status without competing with the subject line for attention.
const STATUS_PILL: Record<string, string> = {
  draft: "border-base-content/20 bg-base-content/5 text-base-content/70",
  sent: "border-success/30 bg-success/10 text-success",
  failed: "border-error/30 bg-error/10 text-error",
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function shortAgent(ua: string | null): string {
  if (!ua) return "unknown client";
  // Trim to a useful prefix; the full UA goes in the title attribute.
  return ua.length > 60 ? ua.slice(0, 60) + "…" : ua;
}

export default function ReachOutList({ initial }: Props) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [eventsById, setEventsById] = useState<Record<string, ReachOutEvent[] | "loading" | "error">>({});
  // Inline two-step delete: first click arms the row's delete button (it
  // flips from a ghost icon to a filled red "Confirm?" button) and a
  // second click within 3s actually deletes. Avoids native `confirm()`
  // popups while still preventing accidental clicks.
  const [confirmingDeleteId, setConfirmingDeleteId] = useState<string | null>(
    null,
  );
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    };
  }, []);

  function toggle(id: string) {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  }

  // Lazily load events the first time a sent row is expanded.
  useEffect(() => {
    Object.entries(expanded).forEach(([id, isOpen]) => {
      if (!isOpen) return;
      if (eventsById[id] !== undefined) return;
      const item = initial.find((i) => i.id === id);
      if (!item || item.status !== "sent") return;

      setEventsById((prev) => ({ ...prev, [id]: "loading" }));
      fetch(`/api/proxy/reach-out/${id}/events`)
        .then(async (res) => {
          if (!res.ok) throw new Error(`fetch failed (${res.status})`);
          const json = (await res.json()) as { events: ReachOutEvent[] };
          setEventsById((prev) => ({ ...prev, [id]: json.events }));
        })
        .catch(() => {
          setEventsById((prev) => ({ ...prev, [id]: "error" }));
        });
    });
  }, [expanded, eventsById, initial]);

  function handleDelete(id: string) {
    if (confirmingDeleteId !== id) {
      // First click: arm the row. Auto-disarm after 3s if the user walks
      // away without confirming.
      if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
      setConfirmingDeleteId(id);
      confirmTimerRef.current = setTimeout(() => {
        setConfirmingDeleteId((current) => (current === id ? null : current));
        confirmTimerRef.current = null;
      }, 3000);
      return;
    }

    // Second click within the 3s window — actually delete.
    if (confirmTimerRef.current) {
      clearTimeout(confirmTimerRef.current);
      confirmTimerRef.current = null;
    }
    setConfirmingDeleteId(null);
    startTransition(async () => {
      try {
        const res = await fetch(`/api/proxy/reach-out/${id}`, {
          method: "DELETE",
        });
        if (!res.ok) throw new Error(`Delete failed (${res.status})`);
        toast.success("Deleted.");
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  function handleEdit(id: string) {
    router.push(`/reach-out?edit=${encodeURIComponent(id)}`, { scroll: false });
    // Smooth-scroll to the composer so the user immediately sees their draft
    // load in. Falls back to instant scroll on browsers without smooth-scroll.
    requestAnimationFrame(() => {
      const el = document.getElementById("reach-out-composer");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  function handleResend(id: string) {
    startTransition(async () => {
      try {
        const res = await fetch(`/api/proxy/reach-out/${id}/send`, {
          method: "POST",
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Send failed (${res.status})`);
        }
        toast.success("Email sent.");
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  if (initial.length === 0) {
    return (
      <div>
        <h2 className="text-xs font-semibold uppercase tracking-widest opacity-50 mb-3">
          Past reach-outs
        </h2>
        <div className="glass-card p-6 text-center text-sm opacity-60">
          No reach-outs yet — generate one above.
        </div>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-xs font-semibold uppercase tracking-widest opacity-50 mb-3">
        Past reach-outs
      </h2>
      <div className="glass-card overflow-hidden divide-y divide-base-300/40">
        {initial.map((r) => {
          const isOpen = expanded[r.id] ?? false;
          const pill =
            STATUS_PILL[r.status] ??
            "border-base-content/20 bg-base-content/5 text-base-content/70";
          const events = eventsById[r.id];
          return (
            <div key={r.id} className="px-5 py-4">
              <div className="flex items-center gap-x-3 gap-y-2 flex-wrap">
                <button
                  type="button"
                  onClick={() => toggle(r.id)}
                  className="btn btn-ghost btn-xs btn-circle shrink-0"
                  aria-label={isOpen ? "Collapse" : "Expand"}
                >
                  {isOpen ? (
                    <ChevronUp className="h-4 w-4" />
                  ) : (
                    <ChevronDown className="h-4 w-4" />
                  )}
                </button>
                <span
                  className={`inline-flex items-center px-2 py-px rounded-full border text-[10px] font-bold uppercase tracking-wide whitespace-nowrap shrink-0 leading-tight ${pill}`}
                >
                  {r.status}
                </span>
                <div className="min-w-0 flex-1 basis-40">
                  <div className="font-medium truncate">{r.subject}</div>
                  <div className="text-xs opacity-60 truncate">
                    {r.recipientName} &lt;{r.recipientEmail}&gt;
                  </div>
                </div>
                {/* Right-cluster: stats + timestamp + actions wrap as one unit
                    so on narrow viewports they drop to a tidy second line
                    instead of fragmenting across three. */}
                <div className="flex items-center gap-x-3 gap-y-2 flex-wrap ml-auto">
                  {r.status === "sent" && (
                    <div className="flex items-center gap-3 text-xs tabular-nums">
                      <span
                        className="inline-flex items-center gap-1 opacity-70"
                        title={
                          r.lastOpenedAt
                            ? `Last opened ${formatTime(r.lastOpenedAt)}`
                            : "Not opened yet"
                        }
                      >
                        <Eye className="h-3.5 w-3.5" />
                        {r.openCount}
                      </span>
                      <span
                        className="inline-flex items-center gap-1 opacity-70"
                        title={
                          r.lastClickedAt
                            ? `Last clicked ${formatTime(r.lastClickedAt)}`
                            : "No link clicks yet"
                        }
                      >
                        <MousePointerClick className="h-3.5 w-3.5" />
                        {r.clickCount}
                      </span>
                    </div>
                  )}
                  <span
                    className="opacity-40 text-xs tabular-nums whitespace-nowrap"
                    title={formatTime(r.createdAt)}
                  >
                    {r.sentAt
                      ? `sent ${formatTime(r.sentAt)}`
                      : formatTime(r.createdAt)}
                  </span>
                  {r.status === "draft" && (
                    <button
                      type="button"
                      onClick={() => handleEdit(r.id)}
                      className="btn btn-primary btn-xs"
                      disabled={isPending}
                      title="Open this draft in the composer to edit and send"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      Edit & send
                    </button>
                  )}
                  {r.status === "failed" && (
                    <button
                      type="button"
                      onClick={() => handleResend(r.id)}
                      className="btn btn-ghost btn-xs"
                      disabled={isPending}
                      title="Retry sending"
                    >
                      <Send className="h-3.5 w-3.5" />
                      Retry
                    </button>
                  )}
                  {confirmingDeleteId === r.id ? (
                    <button
                      type="button"
                      onClick={() => handleDelete(r.id)}
                      className="btn btn-error btn-xs"
                      disabled={isPending}
                      aria-label="Confirm delete"
                      title="Click again to confirm — auto-cancels in 3s"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      Confirm?
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={() => handleDelete(r.id)}
                      className="btn btn-ghost btn-xs btn-circle text-error"
                      disabled={isPending}
                      aria-label="Delete"
                      title="Delete this reach-out"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              </div>

              {isOpen && (
                <div className="mt-3 pl-9 space-y-2">
                  {r.errorMessage && (
                    <div className="text-xs text-error bg-error/5 border border-error/20 rounded p-2">
                      {r.errorMessage}
                    </div>
                  )}
                  <pre className="whitespace-pre-wrap text-sm leading-relaxed bg-base-200/40 rounded p-3 font-sans">
                    {r.body}
                  </pre>

                  {r.status === "sent" && (
                    <div className="rounded border border-base-300/40 bg-base-200/20 p-3 space-y-2">
                      <div className="text-xs uppercase tracking-widest opacity-50 font-medium">
                        Tracking timeline
                      </div>
                      {events === "loading" && (
                        <div className="text-xs opacity-60">Loading events…</div>
                      )}
                      {events === "error" && (
                        <div className="text-xs text-error">
                          Failed to load events.
                        </div>
                      )}
                      {Array.isArray(events) && events.length === 0 && (
                        <div className="text-xs opacity-60">
                          No opens or clicks recorded yet.
                        </div>
                      )}
                      {Array.isArray(events) && events.length > 0 && (
                        <ul className="space-y-1">
                          {events.map((ev) => {
                            const Icon =
                              ev.eventType === "click"
                                ? MousePointerClick
                                : Eye;
                            return (
                              <li
                                key={ev.id}
                                className="text-xs flex items-start gap-2"
                              >
                                <Icon className="h-3.5 w-3.5 mt-0.5 opacity-70 shrink-0" />
                                <div className="min-w-0 flex-1">
                                  <span className="font-medium uppercase tracking-wider opacity-70">
                                    {ev.eventType}
                                  </span>
                                  <span className="opacity-60 ml-2">
                                    {formatTime(ev.createdAt)}
                                  </span>
                                  {ev.trackedUrl && (
                                    <div className="opacity-60 truncate">
                                      → {ev.trackedUrl}
                                    </div>
                                  )}
                                  <div
                                    className="opacity-40 truncate"
                                    title={ev.userAgent ?? undefined}
                                  >
                                    {shortAgent(ev.userAgent)}
                                  </div>
                                </div>
                              </li>
                            );
                          })}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
