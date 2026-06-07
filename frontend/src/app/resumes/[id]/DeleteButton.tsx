"use client";

import { useFormStatus } from "react-dom";

function DeleteSubmit({ label }: { label: string }) {
  const { pending } = useFormStatus();
  return (
    <button
      type="submit"
      disabled={pending}
      onClick={(e) => {
        if (!confirm(`Delete resume "${label}"? This cannot be undone.`)) {
          e.preventDefault();
        }
      }}
      className="btn btn-error btn-sm"
    >
      {pending && <span className="loading loading-spinner loading-xs" />}
      {pending ? "Deleting…" : "Delete resume"}
    </button>
  );
}

export default function DeleteButton({
  action,
  label,
}: {
  action: () => Promise<void>;
  label: string;
}) {
  return (
    <form action={action}>
      <DeleteSubmit label={label} />
    </form>
  );
}
