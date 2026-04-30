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
} from "lucide-react";

type ReachOut = {
  id: string;
  subject: string;
  body: string;
  recipientName: string;
  recipientEmail: string;
};

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
  const [isPending, startTransition] = useTransition();
  const [step, setStep] = useState<"form" | "preview">("form");
  const [loadingEdit, setLoadingEdit] = useState(false);

  const [recipientName, setRecipientName] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
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
    setRecipientName("");
    setRecipientEmail("");
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
    if (!recipientName.trim() || !recipientEmail.trim() || !linkedinProfile.trim()) {
      toast.error("Name, email, and LinkedIn profile text are required.");
      return;
    }
    if (!recipientEmail.includes("@")) {
      toast.error("Enter a valid email address.");
      return;
    }

    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/reach-out/generate", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            recipientName: recipientName.trim(),
            recipientEmail: recipientEmail.trim(),
            linkedinProfile: linkedinProfile.trim(),
            contextNote: contextNote.trim() || null,
            resumeId: resumeId || null,
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
    if (!recipientName.trim() || !recipientEmail.trim()) {
      toast.error("Recipient name and email are required.");
      return;
    }
    if (!recipientEmail.includes("@")) {
      toast.error("Enter a valid email address.");
      return;
    }

    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/reach-out/blank", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            recipientName: recipientName.trim(),
            recipientEmail: recipientEmail.trim(),
            linkedinProfile: linkedinProfile.trim() || null,
            contextNote: contextNote.trim() || null,
            resumeId: resumeId || null,
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
    return (
      <div id="reach-out-composer" className="glass-card p-5 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold">
              {isExistingDraft ? "Edit draft" : "Preview & approve"}
            </h2>
            <p className="text-xs opacity-60">
              To: {draft.recipientName} &lt;{draft.recipientEmail}&gt;
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

        <div>
          <label
            htmlFor="reach-out-subject"
            className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
          >
            Subject
          </label>
          <input
            id="reach-out-subject"
            type="text"
            className="input input-bordered w-full"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
          />
        </div>

        <div>
          <div className="flex items-center justify-between gap-2 mb-1">
            <label
              htmlFor="reach-out-body"
              className="label-text uppercase tracking-widest text-xs opacity-50 font-medium"
            >
              Body
            </label>
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
          </div>
          <textarea
            id="reach-out-body"
            ref={bodyRef}
            className="textarea textarea-bordered w-full font-mono text-sm leading-relaxed"
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
          <p className="text-[11px] opacity-50 mt-1">
            Hyperlinks: type{" "}
            <code className="font-mono">[label](https://url)</code> or use{" "}
            <span className="font-medium">Insert link</span>. Bare URLs are
            auto-linked. All clicks are tracked.
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={handleSend}
            className="btn btn-gradient"
            disabled={
              isPending ||
              !gmailConnected ||
              !trackingReady ||
              !subject.trim() ||
              !body.trim()
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
          <button
            type="button"
            onClick={handleSaveEdits}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
          >
            <Save className="h-4 w-4" />
            Save edits
          </button>
          {!gmailConnected && (
            <span className="text-xs text-warning">
              Gmail isn't connected — add it above before sending.
            </span>
          )}
          {gmailConnected && !trackingReady && (
            <span className="text-xs text-warning">
              Tracking sidecar offline — see tracking-sidecar/README.md.
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
            Paste the recipient's LinkedIn profile (their About / experience
            text). Subject + body are drafted in your voice.
          </p>
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
          Generate email
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
        {!gmailConnected && (
          <span className="text-xs opacity-60">
            (you can still draft without Gmail connected)
          </span>
        )}
      </div>
    </form>
  );
}
