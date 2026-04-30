"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Sparkles, Send, RotateCcw, Save } from "lucide-react";

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
  const [isPending, startTransition] = useTransition();
  const [step, setStep] = useState<"form" | "preview">("form");

  const [recipientName, setRecipientName] = useState("");
  const [recipientEmail, setRecipientEmail] = useState("");
  const [linkedinProfile, setLinkedinProfile] = useState("");
  const [contextNote, setContextNote] = useState("");
  const [resumeId, setResumeId] = useState<string>(resumes[0]?.id ?? "");

  const [draft, setDraft] = useState<ReachOut | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

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

  if (step === "preview" && draft) {
    return (
      <div className="glass-card p-5 space-y-4">
        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div>
            <h2 className="text-base font-semibold">Preview &amp; approve</h2>
            <p className="text-xs opacity-60">
              To: {draft.recipientName} &lt;{draft.recipientEmail}&gt;
            </p>
          </div>
          <button
            type="button"
            onClick={reset}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
          >
            <RotateCcw className="h-4 w-4" />
            Discard &amp; start over
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
          <label
            htmlFor="reach-out-body"
            className="label-text uppercase tracking-widest text-xs opacity-50 font-medium block mb-1"
          >
            Body
          </label>
          <textarea
            id="reach-out-body"
            className="textarea textarea-bordered w-full font-mono text-sm leading-relaxed"
            rows={14}
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <button
            type="button"
            onClick={handleSend}
            className="btn btn-gradient"
            disabled={isPending || !gmailConnected || !trackingReady}
            title={
              !gmailConnected
                ? "Connect Gmail above first"
                : !trackingReady
                  ? "Tracking sidecar offline — deploy tracking-sidecar/"
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
    <form onSubmit={handleGenerate} className="glass-card p-5 space-y-4">
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
          LinkedIn profile text
        </label>
        <textarea
          id="reach-profile"
          className="textarea textarea-bordered w-full text-sm"
          rows={8}
          placeholder="Paste their About section, experience, education…"
          value={linkedinProfile}
          onChange={(e) => setLinkedinProfile(e.target.value)}
          required
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

      <div className="flex items-center gap-2">
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
        {!gmailConnected && (
          <span className="text-xs opacity-60">
            (you can still draft without Gmail connected)
          </span>
        )}
      </div>
    </form>
  );
}
