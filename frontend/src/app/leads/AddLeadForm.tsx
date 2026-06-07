"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { X } from "lucide-react";

type FormState = {
  name: string;
  email: string;
  linkedinUrl: string;
  currentCompany: string;
  role: string;
  linkedinProfile: string;
  notes: string;
};

const EMPTY: FormState = {
  name: "",
  email: "",
  linkedinUrl: "",
  currentCompany: "",
  role: "",
  linkedinProfile: "",
  notes: "",
};

export default function AddLeadForm({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY);

  const setField = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error("Name is required");
      return;
    }
    setBusy(true);
    try {
      const payload: Record<string, string | null> = { name: form.name.trim() };
      for (const [k, v] of Object.entries(form)) {
        if (k === "name") continue;
        const trimmed = (v ?? "").trim();
        if (trimmed) payload[k] = trimmed;
      }
      const res = await fetch("/api/proxy/leads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      toast.success(`Added ${form.name.trim()}`);
      setForm(EMPTY);
      startTransition(() => router.refresh());
      onClose();
    } catch (e) {
      toast.error(`Add failed: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="glass-card p-6 animate-fade-in">
      <div className="flex items-center justify-between mb-5">
        <h2 className="text-base font-semibold tracking-tight">Add a lead</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close"
          className="btn btn-ghost btn-sm btn-circle"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <form onSubmit={onSubmit} className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Field label="Name" required>
            <input
              type="text"
              value={form.name}
              onChange={(e) => setField("name", e.target.value)}
              required
              autoFocus
              className="input input-bordered input-sm w-full"
              placeholder="Jane Doe"
            />
          </Field>

          <Field label="Email">
            <input
              type="email"
              value={form.email}
              onChange={(e) => setField("email", e.target.value)}
              className="input input-bordered input-sm w-full"
              placeholder="jane@company.com"
            />
          </Field>

          <Field label="Current company">
            <input
              type="text"
              value={form.currentCompany}
              onChange={(e) => setField("currentCompany", e.target.value)}
              className="input input-bordered input-sm w-full"
              placeholder="Acme Inc."
            />
          </Field>

          <Field label="Role">
            <input
              type="text"
              value={form.role}
              onChange={(e) => setField("role", e.target.value)}
              className="input input-bordered input-sm w-full"
              placeholder="Senior Engineer"
            />
          </Field>

          <Field label="LinkedIn URL" full>
            <input
              type="url"
              value={form.linkedinUrl}
              onChange={(e) => setField("linkedinUrl", e.target.value)}
              className="input input-bordered input-sm w-full"
              placeholder="https://www.linkedin.com/in/janedoe"
            />
          </Field>
        </div>

        <Field
          label="LinkedIn profile (paste from the profile PDF)"
          help="Paste the raw text. The Reach Out composer reads this when generating personalized emails."
        >
          <textarea
            value={form.linkedinProfile}
            onChange={(e) => setField("linkedinProfile", e.target.value)}
            rows={6}
            className="textarea textarea-bordered textarea-sm w-full font-mono text-xs leading-relaxed"
            placeholder="Paste profile text here…"
          />
        </Field>

        <Field label="Notes">
          <textarea
            value={form.notes}
            onChange={(e) => setField("notes", e.target.value)}
            rows={2}
            className="textarea textarea-bordered textarea-sm w-full"
            placeholder="Anything to remember about this lead"
          />
        </Field>

        <div className="flex justify-end gap-2 pt-2">
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="btn btn-ghost btn-sm"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy || !form.name.trim()}
            className="btn btn-primary btn-sm"
          >
            {busy ? "Saving…" : "Add lead"}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  required,
  full,
  help,
  children,
}: {
  label: string;
  required?: boolean;
  full?: boolean;
  help?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={full ? "md:col-span-2" : ""}>
      <label className="block text-xs font-medium opacity-70 mb-1.5">
        {label}
        {required && <span className="text-error ml-0.5">*</span>}
      </label>
      {children}
      {help && <p className="text-[11px] opacity-50 mt-1">{help}</p>}
    </div>
  );
}
