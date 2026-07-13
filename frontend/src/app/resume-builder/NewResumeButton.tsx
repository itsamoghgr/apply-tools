"use client";

import { useState, useTransition } from "react";
import { Plus, Loader2 } from "lucide-react";
import { createResumeProfile } from "./actions";
import Modal from "./Modal";

type ExistingResume = { id: string; name: string };

export default function NewResumeButton({
  existing = [],
}: {
  existing?: ExistingResume[];
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [pending, start] = useTransition();

  function submit() {
    if (pending) return;
    start(() => createResumeProfile(name, sourceId || undefined));
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn btn-gradient btn-sm gap-2"
      >
        <Plus className="h-4 w-4" />
        New resume
      </button>

      {open && (
        <Modal title="Name your resume" onClose={() => setOpen(false)} maxWidth="max-w-sm">
          <input
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Data Scientist — 2026"
            className="input input-bordered input-sm w-full"
            onKeyDown={(e) => {
              if (e.key === "Enter") submit();
            }}
          />
          {existing.length > 0 && (
            <label className="block">
              <span className="text-xs opacity-60">Start from</span>
              <select
                value={sourceId}
                onChange={(e) => setSourceId(e.target.value)}
                className="select select-bordered select-sm w-full mt-1"
                disabled={pending}
              >
                <option value="">Blank resume</option>
                {existing.map((r) => (
                  <option key={r.id} value={r.id}>
                    Import from: {r.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => setOpen(false)}
              disabled={pending}
            >
              Cancel
            </button>
            <button
              type="button"
              className="btn btn-gradient btn-sm gap-1.5"
              disabled={pending}
              onClick={submit}
            >
              {pending && <Loader2 className="h-4 w-4 animate-spin" />}
              {pending ? "Creating…" : sourceId ? "Import & create" : "Create"}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
