"use client";

import { useEffect, useRef, useState, useTransition } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import {
  Sparkles,
  Send,
  RotateCcw,
  Save,
  Link as LinkIcon,
  Pencil,
  Mail,
  UserPlus,
  MessageSquare,
  Copy,
  ExternalLink,
  CheckCircle2,
} from "lucide-react";

type Channel = "email" | "linkedin_invitation" | "linkedin_message";

type ReachOut = {
  id: string;
  subject: string;
  body: string;
  recipientName: string;
  recipientEmail: string;
  channel?: Channel;
  status?: string;
};

const CHANNELS: {
  id: Channel;
  label: string;
  short: string;
  Icon: typeof Mail;
  description: string;
  bodyLimit: number;
  subjectLimit?: number;
}[] = [
  {
    id: "email",
    label: "Email",
    short: "Email",
    Icon: Mail,
    description: "Send via Gmail with click + open tracking.",
    bodyLimit: 4000,
    subjectLimit: 200,
  },
  {
    id: "linkedin_invitation",
    label: "LinkedIn invite",
    short: "Invite",
    Icon: UserPlus,
    description: "300-char connection note. Copy + paste into LinkedIn.",
    bodyLimit: 300,
  },
  {
    id: "linkedin_message",
    label: "LinkedIn InMail",
    short: "InMail",
    Icon: MessageSquare,
    description: "Longer LinkedIn message. Copy + paste into LinkedIn.",
    bodyLimit: 1900,
    subjectLimit: 200,
  },
];

function channelMeta(c: Channel) {
  return CHANNELS.find((x) => x.id === c) ?? CHANNELS[0];
}

type Resume = { id: string; label: string };

type Props = {
  resumes: Resume[];
  gmailConnected: boolean;
  trackingReady: boolean;
};

export default function ReachOutComposer({
  resumes,
  gmailConnected,
  trackingReady,
}: Props) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const editId = searchParams.get("edit");
  const prefillLeadId = searchParams.get("leadId");
  const linkedApplicationId = searchParams.get("applicationId");
  const [isPending, startTransition] = useTransition();
  const [step, setStep] = useState<"form" | "preview">("form");
  const [loadingEdit, setLoadingEdit] = useState(false);
  const [prefillBadge, setPrefillBadge] = useState<string | null>(null);

  const [channel, setChannel] = useState<Channel>("email");
  const [recipientName, setRecipientName] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [linkedinUrl, setLinkedinUrl] = useState("");
  const [linkedinProfile, setLinkedinProfile] = useState("");
  const [contextNote, setContextNote] = useState("");
  const [resumeId, setResumeId] = useState<string>(resumes[0]?.id ?? "");

  const [draft, setDraft] = useState<ReachOut | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const bodyRef = useRef<HTMLTextAreaElement | null>(null);
  const [linkPopoverOpen, setLinkPopoverOpen] = useState(false);
  const [linkText, setLinkText] = useState("");
  const [linkUrl, setLinkUrl] = useState("");
  const [pendingSelection, setPendingSelection] = useState<{
    start: number;
    end: number;
  } | null>(null);

  function reset() {
    setStep("form");
    setChannel("email");
    setRecipientName("");
    setRecipientEmail("");
    setLinkedinUrl("");
    setLinkedinProfile("");
    setContextNote("");
    setResumeId(resumes[0]?.id ?? "");
    setDraft(null);
    setSubject("");
    setBody("");
    setLinkPopoverOpen(false);
    setLinkText("");
    setLinkUrl("");
    setPendingSelection(null);
    if (editId) {
      // Strip the ?edit= param so a refresh doesn't reload the same draft.
      router.replace("/reach-out", { scroll: false });
    }
  }

  // Load an existing draft into the preview step when the page is opened
  // with `?edit=<id>` (the "Edit" button on the past-reach-outs list).
  useEffect(() => {
    if (!editId) return;
    if (draft?.id === editId) return;

    let cancelled = false;
    setLoadingEdit(true);
    fetch(`/api/proxy/reach-out/${editId}`)
      .then(async (res) => {
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Load failed (${res.status})`);
        }
        const json = (await res.json()) as ReachOut;
        if (cancelled) return;
        setDraft(json);
        setSubject(json.subject);
        setBody(json.body);
        setStep("preview");
      })
      .catch((err) => {
        if (cancelled) return;
        toast.error((err as Error).message);
        router.replace("/reach-out", { scroll: false });
      })
      .finally(() => {
        if (!cancelled) setLoadingEdit(false);
      });

    return () => {
      cancelled = true;
    };
  }, [editId, draft?.id, router]);

  // Prefill the form when arriving via /reach-out?leadId=…&applicationId=…
  // (the "Reach out" button on the Applications page links here).
  useEffect(() => {
    if (editId) return;
    if (!prefillLeadId) return;

    let cancelled = false;
    fetch(`/api/proxy/leads/${encodeURIComponent(prefillLeadId)}`)
      .then(async (res) => {
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Lead load failed (${res.status})`);
        }
        const lead = (await res.json()) as {
          name?: string;
          email?: string | null;
          linkedinProfile?: string | null;
          linkedinUrl?: string | null;
        };
        if (cancelled) return;
        if (lead.name) setRecipientName(lead.name);
        if (lead.email) setRecipientEmail(lead.email);
        if (lead.linkedinUrl) setLinkedinUrl(lead.linkedinUrl);
        const profile = lead.linkedinProfile || lead.linkedinUrl || "";
        if (profile) setLinkedinProfile(profile);
        setPrefillBadge(
          lead.name
            ? `Linked to ${lead.name}${
                linkedApplicationId ? " · this application" : ""
              }`
            : linkedApplicationId
            ? "Linked to this application"
            : null
        );
      })
      .catch((err) => {
        if (!cancelled) toast.error((err as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [editId, prefillLeadId, linkedApplicationId]);

  function openLinkPopover() {
    const ta = bodyRef.current;
    if (!ta) {
      setLinkPopoverOpen(true);
      return;
    }
    const start = ta.selectionStart ?? body.length;
    const end = ta.selectionEnd ?? body.length;
    const selected = body.slice(start, end);
    setPendingSelection({ start, end });
    setLinkText(selected);
    setLinkUrl("");
    setLinkPopoverOpen(true);
  }

  function insertLink(e: React.FormEvent) {
    e.preventDefault();
    const text = linkText.trim();
    let url = linkUrl.trim();
    if (!text || !url) {
      toast.error("Both the display text and URL are required.");
      return;
    }
    if (!/^https?:\/\//i.test(url)) {
      url = `https://${url}`;
    }
    const sel = pendingSelection ?? { start: body.length, end: body.length };
    const md = `[${text}](${url})`;
    const next = body.slice(0, sel.start) + md + body.slice(sel.end);
    setBody(next);
    setLinkPopoverOpen(false);
    setLinkText("");
    setLinkUrl("");
    setPendingSelection(null);
    requestAnimationFrame(() => {
      const ta = bodyRef.current;
      if (!ta) return;
      const cursor = sel.start + md.length;
      ta.focus();
      ta.setSelectionRange(cursor, cursor);
    });
  }

  function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    if (!recipientName.trim() || !linkedinProfile.trim()) {
      toast.error("Name and LinkedIn profile text are required.");
      return;
    }
    if (channel === "email") {
      if (!recipientEmail.trim()) {
        toast.error("Email channel needs a recipient email.");
        return;
      }
      if (!recipientEmail.includes("@")) {
        toast.error("Enter a valid email address.");
        return;
      }
    }

    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/reach-out/generate", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            recipientName: recipientName.trim(),
            recipientEmail: recipientEmail.trim() || null,
            linkedinProfile: linkedinProfile.trim(),
            contextNote: contextNote.trim() || null,
            resumeId: resumeId || null,
            jobApplicationId: linkedApplicationId || null,
            channel,
          }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Generate failed (${res.status})`);
        }
        const json = (await res.json()) as ReachOut;
        setDraft(json);
        setSubject(json.subject);
        setBody(json.body);
        setStep("preview");
        toast.success("Draft ready — review and edit before sending.");
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  function handleComposeManually() {
    if (!recipientName.trim()) {
      toast.error("Recipient name is required.");
      return;
    }
    if (channel === "email") {
      if (!recipientEmail.trim()) {
        toast.error("Email channel needs a recipient email.");
        return;
      }
      if (!recipientEmail.includes("@")) {
        toast.error("Enter a valid email address.");
        return;
      }
    }

    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/reach-out/blank", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            recipientName: recipientName.trim(),
            recipientEmail: recipientEmail.trim() || null,
            linkedinProfile: linkedinProfile.trim() || null,
            contextNote: contextNote.trim() || null,
            resumeId: resumeId || null,
            jobApplicationId: linkedApplicationId || null,
            channel,
          }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Could not create draft (${res.status})`);
        }
        const json = (await res.json()) as ReachOut;
        setDraft(json);
        setSubject(json.subject);
        setBody(json.body);
        setStep("preview");
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  function handleSaveEdits() {
    if (!draft) return;
    startTransition(async () => {
      try {
        const res = await fetch(`/api/proxy/reach-out/${draft.id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Save failed (${res.status})`);
        }
        const json = (await res.json()) as ReachOut;
        setDraft(json);
        toast.success("Draft saved.");
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  async function handleCopyAndMarkSent() {
    if (!draft) return;
    const ch = (draft.channel ?? "email") as Channel;
    if (ch === "email") return;
    const text =
      ch === "linkedin_invitation"
        ? body
        : `${subject ? subject + "\n\n" : ""}${body}`;
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      toast.error("Couldn't copy to clipboard. Select the text manually.");
      return;
    }
    if (linkedinUrl) {
      window.open(linkedinUrl, "_blank", "noopener,noreferrer");
    } else {
      window.open("https://www.linkedin.com/", "_blank", "noopener,noreferrer");
    }
    startTransition(async () => {
      try {
        const saveRes = await fetch(`/api/proxy/reach-out/${draft.id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        if (!saveRes.ok) {
          const payload = await saveRes.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Save failed (${saveRes.status})`);
        }
        const markRes = await fetch(
          `/api/proxy/reach-out/${draft.id}/mark-sent`,
          { method: "POST" },
        );
        if (!markRes.ok) {
          const payload = await markRes.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Mark-sent failed (${markRes.status})`);
        }
        toast.success(
          "Copied. Paste it on LinkedIn — we marked this draft as sent.",
        );
        reset();
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  function handleSend() {
    if (!draft) return;
    if (!gmailConnected) {
      toast.error("Connect Gmail above before sending.");
      return;
    }
    if (!trackingReady) {
      toast.error(
        "Tracking sidecar is offline — deploy tracking-sidecar/ and set TRACKING_BASE_URL in backend/.env.",
      );
      return;
    }
    startTransition(async () => {
      try {
        // Save any pending edits first so the sent email matches the preview.
        const saveRes = await fetch(`/api/proxy/reach-out/${draft.id}`, {
          method: "PATCH",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ subject, body }),
        });
        if (!saveRes.ok) {
          const payload = await saveRes.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Save failed (${saveRes.status})`);
        }
        const sendRes = await fetch(`/api/proxy/reach-out/${draft.id}/send`, {
          method: "POST",
        });
        if (!sendRes.ok) {
          const payload = await sendRes.json().catch(() => ({}));
          throw new Error(payload.detail ?? `Send failed (${sendRes.status})`);
        }
        toast.success(`Email sent to ${draft.recipientEmail}.`);
        reset();
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  if (loadingEdit && !draft) {
    return (
      <div
        id="reach-out-composer"
        className="glass-card p-5 flex items-center gap-3 text-sm opacity-70"
      >
        <span className="loading loading-spinner loading-sm" />
        Loading draft…
      </div>
    );
  }

  if (step === "preview" && draft) {
    const isExistingDraft = draft.status === "draft" && !!editId;
    const draftChannel = (draft.channel ?? "email") as Channel;
    const meta = channelMeta(draftChannel);
    const isEmail = draftChannel === "email";
    const showSubject = draftChannel !== "linkedin_invitation";
    const bodyOver = body.length > meta.bodyLimit;
    const subjectOver =
      meta.subjectLimit !== undefined && subject.length > meta.subjectLimit;
    return (
      <div id="reach-out-composer" className="glass-card p-5 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold">
              {isExistingDraft ? "Edit draft" : "Preview & approve"}
            </h2>
            <p className="text-xs opacity-60 inline-flex items-center gap-2 flex-wrap">
              <span className="inline-flex items-center gap-1 rounded-md border border-base-300/60 bg-base-200/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wider font-medium">
                <meta.Icon className="h-3 w-3" />
                {meta.short}
              </span>
              <span>
                To: {draft.recipientName}
                {isEmail && draft.recipientEmail
                  ? ` <${draft.recipientEmail}>`
                  : ""}
              </span>
            </p>
          </div>
          <button
            type="button"
            onClick={reset}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
            title={
              isExistingDraft
                ? "Close without sending — your edits won't be saved unless you hit Save edits first."
                : "Throw away this draft and start a new one."
            }
          >
            <RotateCcw className="h-4 w-4" />
            {isExistingDraft ? "Close" : "Discard & start over"}
          </button>
        </div>

        {showSubject && (
          <div>
            <div className="flex items-center justify-between gap-2 mb-1">
              <label
                htmlFor="reach-out-subject"
                className="label-text uppercase tracking-widest text-xs opacity-50 font-medium"
              >
                Subject
              </label>
              {meta.subjectLimit !== undefined && (
                <span
                  className={`text-[11px] tabular-nums ${
                    subjectOver ? "text-error" : "opacity-50"
                  }`}
                >
                  {subject.length}/{meta.subjectLimit}
                </span>
              )}
            </div>
            <input
              id="reach-out-subject"
              type="text"
              className={`input input-bordered w-full ${
                subjectOver ? "input-error" : ""
              }`}
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </div>
        )}

        <div>
          <div className="flex items-center justify-between gap-2 mb-1">
            <label
              htmlFor="reach-out-body"
              className="label-text uppercase tracking-widest text-xs opacity-50 font-medium"
            >
              {draftChannel === "linkedin_invitation" ? "Note" : "Body"}
            </label>
            <div className="flex items-center gap-3">
              <span
                className={`text-[11px] tabular-nums ${
                  bodyOver ? "text-error font-medium" : "opacity-50"
                }`}
              >
                {body.length}/{meta.bodyLimit}
              </span>
              {isEmail && (
                <div className="relative">
                  <button
                    type="button"
                    onClick={openLinkPopover}
                    className="btn btn-ghost btn-xs gap-1.5"
                    title="Insert a hyperlink. Tracked clicks are recorded by the sidecar."
                  >
                    <LinkIcon className="h-3.5 w-3.5" />
                    Insert link
                  </button>
                  {linkPopoverOpen && (
                    <form
                      onSubmit={insertLink}
                      className="absolute right-0 top-full mt-1 z-10 w-72 glass-card p-3 space-y-2 shadow-lg"
                    >
                      <div>
                        <label
                          htmlFor="link-text"
                          className="label-text uppercase tracking-widest text-[10px] opacity-50 font-medium block mb-0.5"
                        >
                          Display text
                        </label>
                        <input
                          id="link-text"
                          type="text"
                          autoFocus
                          className="input input-bordered input-sm w-full"
                          placeholder="my portfolio"
                          value={linkText}
                          onChange={(e) => setLinkText(e.target.value)}
                        />
                      </div>
                      <div>
                        <label
                          htmlFor="link-url"
                          className="label-text uppercase tracking-widest text-[10px] opacity-50 font-medium block mb-0.5"
                        >
                          URL
                        </label>
                        <input
                          id="link-url"
                          type="url"
                          className="input input-bordered input-sm w-full"
                          placeholder="https://example.com"
                          value={linkUrl}
                          onChange={(e) => setLinkUrl(e.target.value)}
                        />
                      </div>
                      <div className="flex items-center gap-2 justify-end">
                        <button
                          type="button"
                          onClick={() => {
                            setLinkPopoverOpen(false);
                            setLinkText("");
                            setLinkUrl("");
                            setPendingSelection(null);
                          }}
                          className="btn btn-ghost btn-xs"
                        >
                          Cancel
                        </button>
                        <button type="submit" className="btn btn-primary btn-xs">
                          Insert
                        </button>
                      </div>
                    </form>
                  )}
                </div>
              )}
            </div>
          </div>
          <textarea
            id="reach-out-body"
            ref={bodyRef}
            className={`textarea textarea-bordered w-full font-mono text-sm leading-relaxed ${
              bodyOver ? "textarea-error" : ""
            }`}
            rows={draftChannel === "linkedin_invitation" ? 6 : 14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          {isEmail ? (
            <p className="text-[11px] opacity-50 mt-1">
              Hyperlinks: type{" "}
              <code className="font-mono">[label](https://url)</code> or use{" "}
              <span className="font-medium">Insert link</span>. Bare URLs are
              auto-linked. All clicks are tracked.
            </p>
          ) : (
            <p className="text-[11px] opacity-50 mt-1">
              LinkedIn doesn't render Markdown — keep it plain text. We'll
              copy this to your clipboard so you can paste it on LinkedIn.
            </p>
          )}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {isEmail ? (
            <button
              type="button"
              onClick={handleSend}
              className="btn btn-gradient"
              disabled={
                isPending ||
                !gmailConnected ||
                !trackingReady ||
                !subject.trim() ||
                !body.trim() ||
                bodyOver ||
                subjectOver
              }
              title={
                !gmailConnected
                  ? "Connect Gmail above first"
                  : !trackingReady
                    ? "Tracking sidecar offline — deploy tracking-sidecar/"
                    : !subject.trim()
                      ? "Subject is empty"
                      : !body.trim()
                        ? "Body is empty"
                        : "Send via Gmail"
              }
            >
              {isPending ? (
                <span className="loading loading-spinner loading-xs" />
              ) : (
                <Send className="h-4 w-4" />
              )}
              Send
            </button>
          ) : (
            <button
              type="button"
              onClick={handleCopyAndMarkSent}
              className="btn btn-gradient"
              disabled={
                isPending ||
                !body.trim() ||
                bodyOver ||
                subjectOver ||
                (showSubject && !subject.trim())
              }
              title={
                bodyOver
                  ? `Over the ${meta.bodyLimit}-character limit`
                  : !body.trim()
                    ? "Message is empty"
                    : `Copy text + open LinkedIn${linkedinUrl ? "" : " (homepage)"}`
              }
            >
              {isPending ? (
                <span className="loading loading-spinner loading-xs" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
              Copy &amp; open LinkedIn
              <ExternalLink className="h-3.5 w-3.5 opacity-70" />
            </button>
          )}
          <button
            type="button"
            onClick={handleSaveEdits}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
          >
            <Save className="h-4 w-4" />
            Save edits
          </button>
          {isEmail && !gmailConnected && (
            <span className="text-xs text-warning">
              Gmail isn't connected — add it above before sending.
            </span>
          )}
          {isEmail && gmailConnected && !trackingReady && (
            <span className="text-xs text-warning">
              Tracking sidecar offline — see tracking-sidecar/README.md.
            </span>
          )}
          {!isEmail && (
            <span className="text-xs opacity-60 inline-flex items-center gap-1">
              <CheckCircle2 className="h-3.5 w-3.5" />
              We'll mark this as sent once you copy.
            </span>
          )}
        </div>
      </div>
    );
  }

  return (
    <form
      id="reach-out-composer"
      onSubmit={handleGenerate}
      className="glass-card p-5 space-y-4"
    >
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-primary/10 text-primary">
          <Sparkles className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold">Compose a reach-out</h2>
          <p className="text-xs opacity-60">
            {channelMeta(channel).description}
          </p>
        </div>
      </div>

      {prefillBadge && (
        <div className="inline-flex items-center gap-1.5 rounded-md border border-primary/30 bg-primary/5 px-2.5 py-1 text-xs text-primary self-start">
          <LinkIcon className="h-3 w-3" />
          {prefillBadge}
        </div>
      )}

      <div>
        <span className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1.5">
          Channel
        </span>
        <div role="tablist" className="inline-flex rounded-lg bg-base-200/60 p-1 gap-1">
          {CHANNELS.map((c) => {
            const active = channel === c.id;
            const Icon = c.Icon;
            return (
              <button
                key={c.id}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => setChannel(c.id)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  active
                    ? "bg-base-100 shadow-sm text-base-content"
                    : "text-base-content/60 hover:text-base-content"
                }`}
                title={c.description}
              >
                <Icon className="h-3.5 w-3.5" />
                {c.label}
              </button>
            );
          })}
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label
            htmlFor="reach-name"
            className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
          >
            Name
          </label>
          <input
            id="reach-name"
            type="text"
            className="input input-bordered w-full"
            placeholder="Sam Recruiter"
            value={recipientName}
            onChange={(e) => setRecipientName(e.target.value)}
            required
          />
        </div>
        {channel === "email" ? (
          <div>
            <label
              htmlFor="reach-email"
              className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
            >
              Email
            </label>
            <input
              id="reach-email"
              type="email"
              className="input input-bordered w-full"
              placeholder="sam@company.com"
              value={recipientEmail}
              onChange={(e) => setRecipientEmail(e.target.value)}
              required
            />
          </div>
        ) : (
          <div>
            <label
              htmlFor="reach-linkedin-url"
              className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
            >
              LinkedIn URL{" "}
              <span className="opacity-60 normal-case tracking-normal">
                (optional — opens after copy)
              </span>
            </label>
            <input
              id="reach-linkedin-url"
              type="url"
              className="input input-bordered w-full"
              placeholder="https://www.linkedin.com/in/…"
              value={linkedinUrl}
              onChange={(e) => setLinkedinUrl(e.target.value)}
            />
          </div>
        )}
      </div>

      <div>
        <label
          htmlFor="reach-profile"
          className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
        >
          LinkedIn profile text{" "}
          <span className="opacity-60 normal-case tracking-normal">
            (required for AI draft, optional for manual)
          </span>
        </label>
        <textarea
          id="reach-profile"
          className="textarea textarea-bordered w-full text-sm"
          rows={8}
          placeholder="Paste their About section, experience, education…"
          value={linkedinProfile}
          onChange={(e) => setLinkedinProfile(e.target.value)}
        />
      </div>

      <div className="grid sm:grid-cols-[1fr_220px] gap-3">
        <div>
          <label
            htmlFor="reach-context"
            className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
          >
            Context (optional)
          </label>
          <input
            id="reach-context"
            type="text"
            className="input input-bordered w-full"
            placeholder="e.g. applying for the DS Intern role at Acme; we both went to GW"
            value={contextNote}
            onChange={(e) => setContextNote(e.target.value)}
          />
        </div>
        <div>
          <label
            htmlFor="reach-resume"
            className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
          >
            Resume voice
          </label>
          <select
            id="reach-resume"
            className="select select-bordered w-full"
            value={resumeId}
            onChange={(e) => setResumeId(e.target.value)}
          >
            {resumes.length === 0 && <option value="">— default —</option>}
            {resumes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="submit"
          disabled={isPending}
          className="btn btn-gradient"
        >
          {isPending ? (
            <span className="loading loading-spinner loading-xs" />
          ) : (
            <Sparkles className="h-4 w-4" />
          )}
          {channel === "email"
            ? "Generate email"
            : channel === "linkedin_invitation"
              ? "Generate invite note"
              : "Generate InMail"}
        </button>
        <button
          type="button"
          onClick={handleComposeManually}
          disabled={isPending}
          className="btn btn-ghost btn-sm"
          title="Skip the AI draft and write subject + body yourself"
        >
          <Pencil className="h-4 w-4" />
          Compose manually
        </button>
        {channel === "email" && !gmailConnected && (
          <span className="text-xs opacity-60">
            (you can still draft without Gmail connected)
          </span>
        )}
      </div>
    </form>
  );
}
