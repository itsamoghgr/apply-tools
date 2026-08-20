"use client";

import { useState } from "react";
import { toast } from "sonner";
import { X, Search, Loader2 } from "lucide-react";
import { addWatchedCompany } from "./actions";

type Preview = {
  ats: string;
  slug: string | null;
  ok: boolean;
  total: number;
  matched: number;
  sample: { title: string; role: string; location: string | null }[];
  error: string | null;
};

export default function AddCompanyForm({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: () => void;
}) {
  const [name, setName] = useState("");
  const [careerUrl, setCareerUrl] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [checking, setChecking] = useState(false);
  const [saving, setSaving] = useState(false);

  /**
   * Validate the URL against the live board BEFORE saving. Without this a
   * typo'd URL sits in the watchlist failing silently until you notice the red
   * badge hours later.
   */
  async function check() {
    if (!careerUrl.trim()) {
      toast.error("Enter a career page URL first");
      return;
    }
    setChecking(true);
    setPreview(null);
    try {
      const res = await fetch("/api/agent/api/v1/jobboard/detect", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ career_url: careerUrl.trim() }),
      });
      if (!res.ok) throw new Error();
      setPreview(await res.json());
    } catch {
      toast.error("Could not reach the agent service. Is it running on :8002?");
    } finally {
      setChecking(false);
    }
  }

  async function onSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setSaving(true);
    try {
      const data = new FormData();
      data.set("name", name.trim());
      data.set("careerUrl", careerUrl.trim());
      const res = await addWatchedCompany({}, data);
      if (res.error) {
        toast.error(res.error);
        return;
      }
      toast.success(res.message ?? "Added.");
      onAdded();
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="mb-4 rounded-box border border-base-300 bg-base-200/60 p-4"
    >
      <div className="mb-3 flex items-center justify-between">
        <h3 className="font-semibold">Watch a career page</h3>
        <button type="button" className="btn btn-ghost btn-xs btn-square" onClick={onClose}>
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="form-control">
          <span className="label-text mb-1 text-xs opacity-70">Company name</span>
          <input
            className="input input-bordered input-sm w-full"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Anthropic"
            required
            maxLength={300}
          />
        </label>

        <label className="form-control">
          <span className="label-text mb-1 text-xs opacity-70">Career page URL</span>
          <div className="join w-full">
            <input
              className="input input-bordered input-sm join-item w-full"
              value={careerUrl}
              onChange={(e) => {
                setCareerUrl(e.target.value);
                setPreview(null);
              }}
              placeholder="https://boards.greenhouse.io/anthropic"
              required
              maxLength={2000}
            />
            <button
              type="button"
              className="btn btn-sm join-item"
              onClick={check}
              disabled={checking}
            >
              {checking ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Search className="h-3.5 w-3.5" />
              )}
              Check
            </button>
          </div>
        </label>
      </div>

      <p className="mt-2 text-xs opacity-60">
        Point this at the company&apos;s ATS board (Greenhouse, Lever, Ashby,
        SmartRecruiters) when it has one — those are read exactly and for free.
        Any other page falls back to an LLM scrape each cycle.
      </p>

      {preview && (
        <div
          className={`mt-3 rounded-field border px-3 py-2 text-sm ${
            preview.ok ? "border-success/40 bg-success/5" : "border-error/40 bg-error/5"
          }`}
        >
          {preview.ok ? (
            <>
              <p>
                <span className="font-medium">{preview.ats}</span>
                {preview.slug && <span className="opacity-60"> · {preview.slug}</span>}
                {" — "}
                found <span className="font-medium">{preview.total}</span> jobs,{" "}
                <span className="font-medium">{preview.matched}</span> matching your
                roles.
              </p>
              {preview.sample.length > 0 && (
                <ul className="mt-1.5 space-y-0.5 text-xs opacity-70">
                  {preview.sample.map((s, i) => (
                    <li key={i}>
                      · {s.title}
                      {s.location && <span className="opacity-60"> — {s.location}</span>}
                    </li>
                  ))}
                </ul>
              )}
              {preview.matched === 0 && (
                <p className="mt-1 text-xs opacity-70">
                  Nothing matches right now — that&apos;s fine, you&apos;ll be told when
                  something does.
                </p>
              )}
            </>
          ) : (
            <p className="text-error">{preview.error ?? "Could not read that page."}</p>
          )}
        </div>
      )}

      <div className="mt-4 flex justify-end gap-2">
        <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
          Cancel
        </button>
        <button type="submit" className="btn btn-primary btn-sm" disabled={saving}>
          {saving ? "Adding…" : "Add to watchlist"}
        </button>
      </div>
    </form>
  );
}
