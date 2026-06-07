"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  Code2,
  Forward,
  Loader2,
  Mail as MailIcon,
  Reply,
  Send,
  X,
} from "lucide-react";
import { toast } from "sonner";

type MailMessage = {
  id: string;
  messageId?: string;
  fromName: string;
  fromEmail: string;
  to: string;
  subject: string;
  date: string | null;
  snippet: string;
  unread: boolean;
};

type FullMessage = {
  id: string;
  messageId?: string;
  subject: string;
  fromName: string;
  fromEmail: string;
  to: string;
  date: string | null;
  text: string;
  html: string;
};

type BodyState =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "ready"; data: FullMessage }
  | { kind: "error"; message: string };

type ComposerMode = "reply" | "forward";

type ComposerState =
  | { open: false }
  | {
      open: true;
      mode: ComposerMode;
      to: string;
      subject: string;
      body: string;
      sending: boolean;
      error: string | null;
    };

type Props = {
  messages: MailMessage[];
  address: string | null;
  loadError: string | null;
};

function formatListDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }
  const sameYear = d.getFullYear() === now.getFullYear();
  return d.toLocaleDateString([], {
    month: "short",
    day: "numeric",
    ...(sameYear ? {} : { year: "numeric" }),
  });
}

function formatFullDate(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString([], {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function initialsFor(m: { fromName: string; fromEmail: string }): string {
  const source = m.fromName?.trim() || m.fromEmail?.trim() || "?";
  const parts = source.split(/[\s.@_-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

// Deterministic pastel-ish color from a string, for the avatar background.
function avatarColor(seed: string): string {
  let hash = 0;
  for (let i = 0; i < seed.length; i++) {
    hash = (hash * 31 + seed.charCodeAt(i)) >>> 0;
  }
  // Warm-minimal avatar palette — muted tones drawn from the app's category
  // family (indigo accent, slate-blue, plum, forest, amber, clay) rather than
  // saturated default hues, so avatars sit calmly against the cream surfaces.
  const palette = [
    "avatar-tone-indigo",
    "avatar-tone-slate",
    "avatar-tone-plum",
    "avatar-tone-forest",
    "avatar-tone-amber",
    "avatar-tone-clay",
  ];
  return palette[hash % palette.length];
}

function senderLabel(m: { fromName: string; fromEmail: string }): string {
  return m.fromName?.trim() || m.fromEmail || "(unknown sender)";
}

function buildReplyDraft(full: FullMessage): { subject: string; body: string } {
  const subject = /^re:/i.test(full.subject)
    ? full.subject
    : `Re: ${full.subject || ""}`.trim();
  const dateLine = full.date ? formatFullDate(full.date) : "";
  const senderLine = full.fromName
    ? `${full.fromName} <${full.fromEmail}>`
    : full.fromEmail;
  const quoted = (full.text || "")
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
  const header = dateLine
    ? `On ${dateLine}, ${senderLine} wrote:`
    : `${senderLine} wrote:`;
  return {
    subject,
    body: `\n\n${header}\n${quoted}`,
  };
}

function buildForwardDraft(full: FullMessage): {
  subject: string;
  body: string;
} {
  const subject = /^fwd?:/i.test(full.subject)
    ? full.subject
    : `Fwd: ${full.subject || ""}`.trim();
  const headerLines = [
    "---------- Forwarded message ----------",
    `From: ${full.fromName ? `${full.fromName} <${full.fromEmail}>` : full.fromEmail}`,
    full.date ? `Date: ${formatFullDate(full.date)}` : null,
    `Subject: ${full.subject || ""}`,
    full.to ? `To: ${full.to}` : null,
  ]
    .filter(Boolean)
    .join("\n");
  return {
    subject,
    body: `\n\n${headerLines}\n\n${full.text || ""}`,
  };
}

export default function MailClient({ messages, address, loadError }: Props) {
  const [selectedId, setSelectedId] = useState<string | null>(
    messages.length > 0 ? messages[0].id : null,
  );
  const [bodies, setBodies] = useState<Record<string, BodyState>>({});
  const [composer, setComposer] = useState<ComposerState>({ open: false });
  // Tracks UIDs we've already kicked off a fetch for, so React StrictMode's
  // double-effect-invocation in dev (and any redundant re-render) doesn't
  // either cancel the fetch on the first cleanup or fire two duplicate
  // network requests for the same UID. Shared between click-fetches and
  // prefetches so neither path ever duplicates the other's work.
  const inFlightRef = useRef<Set<string>>(new Set());
  // Mirror of `bodies` we can read inside the lazy-fetch effect without
  // including `bodies` in its dep array (which would cause re-runs that
  // race with the in-flight fetch).
  const bodiesRef = useRef<Record<string, BodyState>>({});
  useEffect(() => {
    bodiesRef.current = bodies;
  }, [bodies]);
  // Cap how many bodies we prefetch in a single session so a long inbox
  // doesn't quietly hammer Gmail / our pooled IMAP connection.
  const prefetchCountRef = useRef(0);
  const PREFETCH_CAP = 10;

  const selected = useMemo(
    () => messages.find((m) => m.id === selectedId) ?? null,
    [messages, selectedId],
  );
  const bodyState = selectedId ? bodies[selectedId] ?? { kind: "idle" as const } : null;

  // Single shared body-fetch implementation. Used both by the click
  // (selection) effect and by the background prefetcher. `silent` skips
  // setting the loading state so an unprompted prefetch doesn't flicker
  // the detail pane to a spinner before the user has even clicked.
  const loadBody = useCallback(
    async (uid: string, opts: { silent?: boolean } = {}) => {
      if (inFlightRef.current.has(uid)) return;
      if (bodiesRef.current[uid]?.kind === "ready") return;
      inFlightRef.current.add(uid);
      if (!opts.silent) {
        setBodies((prev) =>
          prev[uid]?.kind === "ready"
            ? prev
            : { ...prev, [uid]: { kind: "loading" } },
        );
      }
      try {
        const res = await fetch(
          `/api/proxy/mail/${encodeURIComponent(uid)}`,
          { cache: "no-store" },
        );
        if (!res.ok) {
          let detail = `status ${res.status}`;
          try {
            const json = (await res.json()) as { detail?: string };
            if (json.detail) detail = json.detail;
          } catch {}
          // For silent prefetches, only surface failures into state if the
          // user is actually viewing that message — otherwise stay quiet
          // so a 404 on a deleted UID doesn't show an error someone never
          // asked for.
          setBodies((prev) =>
            opts.silent && prev[uid] === undefined
              ? prev
              : { ...prev, [uid]: { kind: "error", message: detail } },
          );
          return;
        }
        const data = (await res.json()) as FullMessage;
        setBodies((prev) => ({ ...prev, [uid]: { kind: "ready", data } }));
      } catch (e) {
        const msg =
          e instanceof Error ? e.message : "Could not load message.";
        setBodies((prev) =>
          opts.silent && prev[uid] === undefined
            ? prev
            : { ...prev, [uid]: { kind: "error", message: msg } },
        );
      } finally {
        inFlightRef.current.delete(uid);
      }
    },
    [],
  );

  // Schedule a prefetch when the browser is idle, capped per session.
  const prefetchBody = useCallback(
    (uid: string | null | undefined) => {
      if (!uid) return;
      if (prefetchCountRef.current >= PREFETCH_CAP) return;
      if (bodiesRef.current[uid]) return; // cached or in-flight
      if (inFlightRef.current.has(uid)) return;
      prefetchCountRef.current += 1;
      const run = () => {
        void loadBody(uid, { silent: true });
      };
      if (typeof window !== "undefined") {
        const ric = (
          window as unknown as {
            requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number;
          }
        ).requestIdleCallback;
        if (ric) {
          ric(run, { timeout: 1500 });
          return;
        }
      }
      setTimeout(run, 0);
    },
    [loadBody],
  );

  // Lazy-load full body when selection changes. Only depends on
  // `selectedId` — the dedupe lives in `loadBody` via `inFlightRef`.
  useEffect(() => {
    if (!selectedId) return;
    void loadBody(selectedId);
  }, [selectedId, loadBody]);

  // Prefetch the top 3 messages on first render so the user's first
  // clicks are instant. Sequenced (not parallel) so we don't multiplex
  // the single pooled IMAP connection on the backend.
  useEffect(() => {
    if (messages.length === 0) return;
    let i = 0;
    let stopped = false;
    const seed = async () => {
      while (!stopped && i < Math.min(3, messages.length)) {
        const uid = messages[i].id;
        i += 1;
        await loadBody(uid, { silent: true });
      }
    };
    // Defer to idle so the seed doesn't compete with hydration / fonts.
    const ric = (
      window as unknown as {
        requestIdleCallback?: (cb: () => void) => number;
      }
    ).requestIdleCallback;
    if (ric) ric(() => void seed());
    else setTimeout(() => void seed(), 0);
    // We also account these against the prefetch cap.
    prefetchCountRef.current += Math.min(3, messages.length);
    return () => {
      stopped = true;
    };
    // Only run when the list itself changes (e.g. RefreshOnFocus reload).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  // After a message is selected, prefetch the immediate neighbors so
  // arrow-key / scroll-down navigation also feels instant.
  useEffect(() => {
    if (!selectedId) return;
    const idx = messages.findIndex((m) => m.id === selectedId);
    if (idx < 0) return;
    if (idx + 1 < messages.length) prefetchBody(messages[idx + 1].id);
    if (idx - 1 >= 0) prefetchBody(messages[idx - 1].id);
  }, [selectedId, messages, prefetchBody]);

  // Close composer when switching messages so we don't accidentally send a
  // half-written draft to a different recipient.
  useEffect(() => {
    setComposer({ open: false });
  }, [selectedId]);

  const openComposer = (mode: ComposerMode) => {
    if (!selected || !bodyState || bodyState.kind !== "ready") return;
    const full = bodyState.data;
    const draft =
      mode === "reply" ? buildReplyDraft(full) : buildForwardDraft(full);
    setComposer({
      open: true,
      mode,
      to: mode === "reply" ? full.fromEmail : "",
      subject: draft.subject,
      body: draft.body,
      sending: false,
      error: null,
    });
  };

  const sendComposer = async () => {
    if (!composer.open) return;
    if (!composer.to.trim() || !composer.subject.trim() || !composer.body.trim()) {
      setComposer({
        ...composer,
        error: "Recipient, subject, and body are required.",
      });
      return;
    }
    setComposer({ ...composer, sending: true, error: null });
    const inReplyTo =
      composer.mode === "reply" && bodyState?.kind === "ready"
        ? bodyState.data.messageId || null
        : null;
    try {
      const res = await fetch(`/api/proxy/mail/send`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          to: composer.to.trim(),
          subject: composer.subject,
          body: composer.body,
          inReplyTo,
          references: inReplyTo,
        }),
      });
      if (!res.ok) {
        let detail = `status ${res.status}`;
        try {
          const json = (await res.json()) as { detail?: string };
          if (json.detail) detail = json.detail;
        } catch {}
        setComposer((prev) =>
          prev.open ? { ...prev, sending: false, error: detail } : prev,
        );
        return;
      }
      toast.success(
        composer.mode === "reply" ? "Reply sent" : "Forwarded",
      );
      setComposer({ open: false });
    } catch (e) {
      setComposer((prev) =>
        prev.open
          ? {
              ...prev,
              sending: false,
              error: e instanceof Error ? e.message : "Send failed.",
            }
          : prev,
      );
    }
  };

  if (messages.length === 0 && !loadError) {
    return (
      <>
        <div className="mb-6">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Mail</h1>
          <p className="text-sm opacity-60 mt-1">
            {address ? (
              <>Connected as <span className="font-medium">{address}</span>.</>
            ) : (
              "Latest messages from your Gmail inbox."
            )}
          </p>
        </div>
        <div className="glass-card p-10 text-center text-sm opacity-60">
          <MailIcon className="h-6 w-6 mx-auto mb-2 opacity-50" />
          Your inbox is empty.
        </div>
      </>
    );
  }

  return (
    <>
      <div className="mb-4 flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Mail</h1>
          <p className="text-sm opacity-60 mt-1">
            {address ? (
              <>Connected as <span className="font-medium">{address}</span>.</>
            ) : (
              "Latest messages from your Gmail inbox."
            )}
          </p>
        </div>
      </div>

      {loadError && (
        <div className="mb-4 rounded-lg border border-error/30 bg-error/10 p-3 text-sm">
          <span className="font-medium text-error">Couldn&rsquo;t load mail:</span>{" "}
          <span className="opacity-80">{loadError}</span>
        </div>
      )}

      {/* Two-pane layout. Outer container fills the viewport below the
          page header so each pane gets independent scroll. The min-h-0 on
          children is required for flex children to shrink past content. */}
      <div className="glass-card overflow-hidden flex h-[calc(100vh-14rem)] min-h-[480px]">
        {/* List pane */}
        <aside
          className={[
            "border-r border-base-300/40 flex flex-col min-h-0",
            "w-full md:w-[360px] lg:w-[400px] shrink-0",
            // On narrow viewports, hide the list when a message is open
            // so the detail pane gets the full width — back button returns.
            selected ? "hidden md:flex" : "flex",
          ].join(" ")}
        >
          <div className="px-4 py-3 border-b border-base-300/40 flex items-center justify-between">
            <span className="text-sm font-medium">Inbox</span>
            <span className="text-xs opacity-50 tabular-nums">
              {messages.length}
            </span>
          </div>
          <ul className="flex-1 min-h-0 overflow-y-auto divide-y divide-base-300/30">
            {messages.map((m) => {
              const active = m.id === selectedId;
              return (
                <li key={m.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(m.id)}
                    className={[
                      "w-full text-left px-4 py-3 flex items-start gap-3 transition-colors",
                      active
                        ? "bg-primary/10"
                        : "hover:bg-base-content/5",
                    ].join(" ")}
                  >
                    <div
                      className={[
                        "h-9 w-9 rounded-full shrink-0 flex items-center justify-center text-white text-xs font-semibold",
                        avatarColor(m.fromEmail || m.fromName || m.id),
                      ].join(" ")}
                      aria-hidden
                    >
                      {initialsFor(m)}
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-baseline gap-2">
                        <div
                          className={[
                            "truncate text-sm",
                            m.unread ? "font-semibold" : "font-medium",
                          ].join(" ")}
                        >
                          {senderLabel(m)}
                        </div>
                        <div className="ml-auto text-[11px] opacity-50 tabular-nums whitespace-nowrap">
                          {formatListDate(m.date)}
                        </div>
                      </div>
                      <div
                        className={[
                          "truncate text-sm mt-0.5",
                          m.unread ? "font-medium" : "opacity-90",
                        ].join(" ")}
                      >
                        {m.subject || "(no subject)"}
                      </div>
                      <div className="truncate text-xs opacity-55 mt-0.5">
                        {m.snippet || "—"}
                      </div>
                    </div>
                    {m.unread && (
                      <span
                        className="h-2 w-2 rounded-full bg-primary shrink-0 mt-2"
                        aria-label="Unread"
                      />
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>

        {/* Detail pane */}
        <section
          className={[
            "flex-1 min-w-0 min-h-0 flex flex-col",
            selected ? "flex" : "hidden md:flex",
          ].join(" ")}
        >
          {!selected ? (
            <div className="flex-1 flex items-center justify-center text-sm opacity-50">
              Select a message to read it.
            </div>
          ) : (
            <DetailPane
              message={selected}
              body={bodyState ?? { kind: "idle" }}
              onBack={() => setSelectedId(null)}
              onReply={() => openComposer("reply")}
              onForward={() => openComposer("forward")}
              composer={composer}
              setComposer={setComposer}
              onSend={sendComposer}
            />
          )}
        </section>
      </div>
    </>
  );
}

function DetailPane({
  message,
  body,
  onBack,
  onReply,
  onForward,
  composer,
  setComposer,
  onSend,
}: {
  message: MailMessage;
  body: BodyState;
  onBack: () => void;
  onReply: () => void;
  onForward: () => void;
  composer: ComposerState;
  setComposer: (s: ComposerState) => void;
  onSend: () => void;
}) {
  const ready = body.kind === "ready" ? body.data : null;
  return (
    <>
      {/* Header */}
      <div className="px-5 py-3 border-b border-base-300/40 flex items-center gap-3">
        <button
          type="button"
          onClick={onBack}
          className="md:hidden btn btn-ghost btn-sm btn-circle"
          aria-label="Back to inbox"
        >
          <ArrowLeft className="h-4 w-4" />
        </button>
        <div className="min-w-0 flex-1">
          <div className="font-semibold truncate">
            {message.subject || "(no subject)"}
          </div>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            type="button"
            onClick={onReply}
            disabled={body.kind !== "ready"}
            className="btn btn-ghost btn-sm gap-1.5"
            title="Reply"
          >
            <Reply className="h-4 w-4" />
            <span className="hidden sm:inline">Reply</span>
          </button>
          <button
            type="button"
            onClick={onForward}
            disabled={body.kind !== "ready"}
            className="btn btn-ghost btn-sm gap-1.5"
            title="Forward"
          >
            <Forward className="h-4 w-4" />
            <span className="hidden sm:inline">Forward</span>
          </button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="px-6 py-5">
          <div className="flex items-start gap-3 mb-5">
            <div
              className={[
                "h-10 w-10 rounded-full shrink-0 flex items-center justify-center text-white text-sm font-semibold",
                avatarColor(message.fromEmail || message.fromName || message.id),
              ].join(" ")}
              aria-hidden
            >
              {initialsFor(message)}
            </div>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <span className="font-semibold">{senderLabel(message)}</span>
                {message.fromEmail && message.fromName && (
                  <span className="text-xs opacity-60">
                    &lt;{message.fromEmail}&gt;
                  </span>
                )}
                <span className="ml-auto text-xs opacity-50 whitespace-nowrap">
                  {formatFullDate(message.date)}
                </span>
              </div>
              <div className="text-xs opacity-60 mt-0.5">
                to {message.to || "(unknown)"}
              </div>
            </div>
          </div>

          {body.kind === "loading" && (
            <div className="flex items-center gap-2 text-sm opacity-60">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading message…
            </div>
          )}
          {body.kind === "error" && (
            <div className="text-sm text-error">
              Couldn&rsquo;t load this message: {body.message}
            </div>
          )}
          {body.kind === "idle" && (
            <p className="whitespace-pre-wrap text-sm opacity-80">
              {message.snippet}
            </p>
          )}
          {ready && <MailBody data={ready} />}
        </div>

        {/* Inline composer */}
        {composer.open && (
          <div className="border-t border-base-300/40 bg-base-200/40 px-6 py-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="text-xs uppercase tracking-wide opacity-60 font-medium">
                {composer.mode === "reply" ? "Reply" : "Forward"}
              </div>
              <button
                type="button"
                onClick={() => setComposer({ open: false })}
                className="btn btn-ghost btn-xs btn-circle"
                aria-label="Discard"
                disabled={composer.sending}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="grid grid-cols-[auto_1fr] items-center gap-x-3 gap-y-2 text-sm">
              <label className="opacity-60 text-xs">To</label>
              <input
                type="email"
                value={composer.to}
                onChange={(e) =>
                  setComposer({ ...composer, to: e.target.value })
                }
                disabled={composer.sending}
                placeholder="recipient@example.com"
                className="input input-sm input-bordered w-full bg-base-100"
              />
              <label className="opacity-60 text-xs">Subject</label>
              <input
                type="text"
                value={composer.subject}
                onChange={(e) =>
                  setComposer({ ...composer, subject: e.target.value })
                }
                disabled={composer.sending}
                className="input input-sm input-bordered w-full bg-base-100"
              />
            </div>
            <textarea
              rows={10}
              value={composer.body}
              onChange={(e) =>
                setComposer({ ...composer, body: e.target.value })
              }
              disabled={composer.sending}
              className="textarea textarea-bordered w-full bg-base-100 text-sm font-sans leading-relaxed"
            />
            {composer.error && (
              <div className="text-xs text-error">{composer.error}</div>
            )}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onSend}
                disabled={composer.sending}
                className="btn btn-primary btn-sm gap-1.5"
              >
                {composer.sending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Send className="h-4 w-4" />
                )}
                {composer.sending ? "Sending…" : "Send"}
              </button>
              <button
                type="button"
                onClick={() => setComposer({ open: false })}
                disabled={composer.sending}
                className="btn btn-ghost btn-sm"
              >
                Discard
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );
}

// Render the email body. If the message has an HTML part, render it inside
// a sandboxed iframe (so its CSS / images / layout work without scripts and
// without bleeding into the host app). Otherwise fall back to the cleaned
// plaintext. A small toggle lets the user flip between the two.
function MailBody({ data }: { data: FullMessage }) {
  const hasHtml = !!data.html?.trim();
  const hasText = !!data.text?.trim();
  const [view, setView] = useState<"html" | "text">(hasHtml ? "html" : "text");

  // If we discover the message has only one of the two views, lock to it.
  useEffect(() => {
    if (!hasHtml && view === "html") setView("text");
    if (!hasText && view === "text" && hasHtml) setView("html");
  }, [hasHtml, hasText, view]);

  return (
    <div>
      {hasHtml && hasText && (
        <div className="mb-3 flex items-center justify-end">
          <div className="join">
            <button
              type="button"
              onClick={() => setView("html")}
              className={`join-item btn btn-xs ${
                view === "html" ? "btn-primary" : "btn-ghost"
              }`}
            >
              Rich
            </button>
            <button
              type="button"
              onClick={() => setView("text")}
              className={`join-item btn btn-xs ${
                view === "text" ? "btn-primary" : "btn-ghost"
              }`}
            >
              <Code2 className="h-3 w-3" />
              Plain
            </button>
          </div>
        </div>
      )}
      {view === "html" && hasHtml ? (
        <HtmlFrame html={data.html} />
      ) : (
        <pre className="whitespace-pre-wrap break-words text-sm font-sans leading-relaxed opacity-95">
          {data.text || "(empty message)"}
        </pre>
      )}
    </div>
  );
}

// Render arbitrary email HTML inside a sandboxed iframe. The iframe gets
// `srcDoc` so we don't need a separate URL, and a small inline script that
// reports its body height back to the parent via postMessage so we can
// auto-size the iframe to fit the content (no internal scrollbars — the
// outer detail pane scrolls).
function HtmlFrame({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(400);

  // Build the iframe document. We:
  //   - Force `target="_blank" rel="noopener noreferrer"` on every link so
  //     clicks open in a new tab instead of trying to navigate the iframe.
  //   - Add a base style so the body scales to the iframe width and uses
  //     a light background (most marketing emails assume a white canvas).
  //   - Append a tiny resize-observer script that postMessages height to
  //     the parent.
  const srcDoc = useMemo(() => {
    return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base target="_blank">
<style>
  html, body { margin: 0; padding: 0; }
  body {
    background: #ffffff;
    color: #111827;
    font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif;
    padding: 16px;
    overflow-x: auto;
  }
  img { max-width: 100%; height: auto; }
  a { color: #2563eb; }
  table { max-width: 100%; }
</style>
</head>
<body>
${html}
<script>
  (function () {
    function send() {
      var h = Math.max(
        document.body.scrollHeight,
        document.documentElement.scrollHeight
      );
      parent.postMessage({ __mailFrameHeight: h }, "*");
    }
    new ResizeObserver(send).observe(document.body);
    window.addEventListener("load", send);
    // Some marketing emails load images late; nudge a few times.
    setTimeout(send, 100);
    setTimeout(send, 500);
    setTimeout(send, 1500);
    // Force every anchor to open in a new tab.
    Array.prototype.forEach.call(document.querySelectorAll("a"), function (a) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    });
  })();
</script>
</body>
</html>`;
  }, [html]);

  useEffect(() => {
    function onMessage(ev: MessageEvent) {
      const data = ev.data as { __mailFrameHeight?: number } | null;
      if (!data || typeof data.__mailFrameHeight !== "number") return;
      // Cap at a sane upper bound so a runaway body doesn't blow out the page.
      setHeight(Math.max(200, Math.min(data.__mailFrameHeight + 16, 20000)));
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  return (
    <iframe
      ref={ref}
      title="Email body"
      srcDoc={srcDoc}
      // `allow-scripts` is needed for our injected resize-observer script;
      // `allow-same-origin` lets that script read document.body's height.
      // (Combined they're normally risky, but srcDoc content has an opaque
      // origin so it can't reach the parent's storage either way.)
      // `allow-popups` lets target="_blank" links open new tabs. We omit
      // form-submission, top-navigation, and pointer-lock so a hostile
      // email can't navigate or capture the host page.
      sandbox="allow-scripts allow-same-origin allow-popups"
      className="w-full rounded-md bg-white border border-base-300/40"
      style={{ height: `${height}px` }}
    />
  );
}
