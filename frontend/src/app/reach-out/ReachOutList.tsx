"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Trash2,
  ChevronDown,
  ChevronUp,
  Send,
  Eye,
  MousePointerClick,
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

const STATUS_BADGE: Record<string, string> = {
  draft: "badge-ghost",
  sent: "badge-success",
  failed: "badge-error",
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
    if (!confirm("Delete this reach-out?")) return;
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
          const badge = STATUS_BADGE[r.status] ?? "badge-ghost";
          const events = eventsById[r.id];
          return (
            <div key={r.id} className="px-5 py-3">
              <div className="flex items-center gap-3 flex-wrap">
                <button
                  type="button"
                  onClick={() => toggle(r.id)}
                  className="btn btn-ghost btn-xs btn-circle"
                  aria-label={isOpen ? "Collapse" : "Expand"}
                >
                  {isOpen ? (
                    <ChevronUp className="h-4 w-4" />
                  ) : (
                    <ChevronDown className="h-4 w-4" />
                  )}
                </button>
                <span className={`badge ${badge} badge-sm uppercase tracking-wider`}>
                  {r.status}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="font-medium truncate">{r.subject}</div>
                  <div className="text-xs opacity-60 truncate">
                    {r.recipientName} &lt;{r.recipientEmail}&gt;
                  </div>
                </div>
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
                <button
                  type="button"
                  onClick={() => handleDelete(r.id)}
                  className="btn btn-ghost btn-xs btn-circle text-error"
                  disabled={isPending}
                  aria-label="Delete"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
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
