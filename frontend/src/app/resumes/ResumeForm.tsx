"use client";

import { useActionState, useEffect } from "react";
import { useFormStatus } from "react-dom";
import { toast } from "sonner";
import type { FormState } from "./actions";

type Props = {
  action: (state: FormState, formData: FormData) => Promise<FormState>;
  initial?: { id?: string; label?: string; content?: string; isActive?: boolean };
  showIdField?: boolean;
  submitLabel: string;
  successMessage?: string;
};

function FieldLabel({
  htmlFor,
  children,
}: {
  htmlFor: string;
  children: React.ReactNode;
}) {
  return (
    <label htmlFor={htmlFor} className="label">
      <span className="label-text uppercase tracking-widest text-xs opacity-50 font-medium">
        {children}
      </span>
    </label>
  );
}

function SubmitButton({ label }: { label: string }) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending} className="btn btn-gradient">
      {pending && <span className="loading loading-spinner loading-xs" />}
      {pending ? "Saving…" : label}
    </button>
  );
}

export default function ResumeForm({
  action,
  initial,
  showIdField = false,
  submitLabel,
  successMessage,
}: Props) {
  const [state, formAction] = useActionState<FormState, FormData>(action, {});

  useEffect(() => {
    if (state.error) toast.error(state.error);
    else if (state.ok && successMessage) toast.success(successMessage);
  }, [state, successMessage]);

  return (
    <form action={formAction} className="space-y-5">
      {showIdField && (
        <div>
          <FieldLabel htmlFor="id">ID (slug)</FieldLabel>
          <input
            id="id"
            name="id"
            defaultValue={initial?.id}
            placeholder="e.g. data-science"
            className="input input-bordered w-full font-mono glow-ring"
            required
          />
          <p className="text-xs opacity-50 mt-1.5">
            Lowercase letters, digits, hyphens, underscores. Used as the
            resume_id in the API.
          </p>
        </div>
      )}
      <div>
        <FieldLabel htmlFor="label">Label</FieldLabel>
        <input
          id="label"
          name="label"
          defaultValue={initial?.label}
          placeholder="e.g. Data Science (v6)"
          className="input input-bordered w-full glow-ring"
          required
        />
      </div>
      <div>
        <FieldLabel htmlFor="content">Content</FieldLabel>
        <textarea
          id="content"
          name="content"
          defaultValue={initial?.content}
          rows={28}
          className="textarea textarea-bordered w-full font-mono text-xs leading-relaxed glow-ring"
          required
        />
      </div>
      <label className="flex items-center gap-2.5 text-sm select-none">
        <input
          name="isActive"
          type="checkbox"
          defaultChecked={initial?.isActive ?? true}
          className="checkbox checkbox-primary checkbox-sm"
        />
        Active (selectable in generators)
      </label>
      <SubmitButton label={submitLabel} />
    </form>
  );
}
