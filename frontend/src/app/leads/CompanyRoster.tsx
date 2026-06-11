"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Users,
  Loader2,
  Mail,
  Check,
  RotateCw,
  UserX,
} from "lucide-react";
import ConfidenceBadge from "./ConfidenceBadge";

// Method labels mirror FindEmailsProgress.METHOD_LABEL so the roster speaks the
// same language as the bulk Find-emails panel (open web first, then verify).
const METHOD_LABEL: Record<string, string> = {
  web_snippet: "open web",
  smtp: "verified (MX)",
  pattern: "pattern",
  apollo: "Apollo",
  hunter: "Hunter",
  abstract: "Abstract",
  agent: "agent",
};

// One person returned by the roster endpoint.
type RosterPerson = {
  name: string;
  title: string;
  email: string | null;
  score: number;
  method: string;
};

// POST /api/agent/api/v1/companies/roster response shape (verbatim from the
// backend contract). Never 500s; a miss is people:[] / count:0.
type RosterResponse = {
  domain: string | null;
  company: string | null;
  people: RosterPerson[];
  count: number;
};

// Per-person save state for the inline "saved / already a lead" affordance.
type SaveState = "idle" | "saving" | "saved" | "exists";

export default function CompanyRoster({
  domain,
  company,
  colSpan,
}: {
  domain: string | null;
  company: string | null;
  colSpan: number;
}) {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [people, setPeople] = useState<RosterPerson[]>([]);
  // Save state keyed by email (the @unique field used for dedupe).
  const [saved, setSaved] = useState<Record<string, SaveState>>({});

  // Persist one roster person as a Lead, mirroring AddLeadForm/LeadsTable: POST
  // to the platform proxy with source:"roster". A 409 means the email is
  // already a lead (email is @unique) — treat that as success/skip, not error.
  async function persist(p: RosterPerson) {
    if (!p.email) return "idle" as SaveState;
    setSaved((s) => ({ ...s, [p.email as string]: "saving" }));
    try {
      const res = await fetch("/api/proxy/leads", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          name: p.name,
          email: p.email,
          role: p.title,
          currentCompany: company,
          source: "roster",
        }),
      });
      if (res.status === 409) {
        setSaved((s) => ({ ...s, [p.email as string]: "exists" }));
        return "exists";
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      setSaved((s) => ({ ...s, [p.email as string]: "saved" }));
      return "saved";
    } catch {
      // Leave the row un-saved so the user can retry via the per-row button.
      setSaved((s) => ({ ...s, [p.email as string]: "idle" }));
      return "idle";
    }
  }

  // Fetch the roster, then auto-save every person who has an email. The endpoint
  // is synchronous — the whole list (with server-side-found emails) arrives in
  // one response, so we render results and persist in one pass. Note: any state
  // resets (loading/error) are done by the caller, not synchronously here, so
  // the initial-mount effect doesn't trigger a cascading render.
  async function load() {
    try {
      const res = await fetch("/api/agent/api/v1/companies/roster", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ domain, company }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const data: RosterResponse = await res.json();
      const found = data.people ?? [];
      setPeople(found);
      setLoading(false);

      // Persist everyone with an email; tally newly-saved (409s don't count).
      const withEmail = found.filter((p) => p.email);
      let newlySaved = 0;
      for (const p of withEmail) {
        const result = await persist(p);
        if (result === "saved") newlySaved += 1;
      }
      if (newlySaved > 0) {
        toast.success(
          `Saved ${newlySaved} lead${newlySaved === 1 ? "" : "s"} from ${
            data.company ?? company ?? "company"
          }.`
        );
        router.refresh();
      }
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  }

  // Run once on expand. `loading` already starts true, so the effect only kicks
  // off the async fetch without synchronously setting state (avoids cascading
  // renders). Explicit retry resets loading/error before re-running.
  useEffect(() => {
    // load() only sets state after its first `await` (the fetch), so it doesn't
    // synchronously cascade — but the rule can't prove that, hence the disable.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    // Intentionally run once on mount only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function retry() {
    setLoading(true);
    setError(null);
    load();
  }

  const label = company ?? domain ?? "this company";

  return (
    <tr>
      <td colSpan={colSpan} className="!p-0 bg-base-200/30">
        <div className="px-4 py-3 border-t border-base-300/40">
          {/* Loading: indeterminate spinner + company name. The roster comes
              back in one synchronous (but slow) response, so we can't stream
              per-person rows — a clear "Finding people…" state is enough. */}
          {loading ? (
            <div className="flex items-center gap-3 py-2">
              <span className="grid place-items-center h-9 w-9 rounded-xl bg-primary/10 text-primary shrink-0">
                <Loader2 className="h-4.5 w-4.5 animate-spin" />
              </span>
              <div className="min-w-0">
                <div className="text-sm font-medium leading-tight">
                  Finding people at {label}…
                </div>
                <div className="text-xs opacity-55">
                  Enumerating recruiters &amp; eng leadership, then finding each
                  email — open web first, then verify. This can take a moment.
                </div>
              </div>
            </div>
          ) : error ? (
            // Fetch failure: inline retry, no crash, no page-blocking toast.
            <div className="flex items-center justify-between gap-3 py-2">
              <div className="text-sm opacity-70">
                Couldn&apos;t load the roster for {label}.
                <span className="opacity-50"> ({error})</span>
              </div>
              <button
                type="button"
                onClick={retry}
                className="btn btn-ghost btn-xs gap-1.5"
              >
                <RotateCw className="h-3.5 w-3.5" />
                Retry
              </button>
            </div>
          ) : people.length === 0 ? (
            // Empty roster is a normal outcome — clean state, never an error.
            <div className="flex items-center gap-3 py-3 text-sm opacity-60">
              <UserX className="h-4 w-4 shrink-0 opacity-50" />
              No recruiting / eng-leadership contacts found at this company.
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center gap-2 text-xs uppercase tracking-wide opacity-55 px-1">
                <Users className="h-3.5 w-3.5" />
                {people.length} contact{people.length === 1 ? "" : "s"} at{" "}
                {label}
              </div>
              <div className="overflow-x-auto rounded-xl border border-base-300/50 bg-base-100/40">
                <table className="table table-sm">
                  <thead>
                    <tr className="text-[11px] uppercase tracking-wide opacity-55">
                      <th>Name</th>
                      <th>Title</th>
                      <th>Email</th>
                      <th>Confidence</th>
                      <th>Method</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {people.map((p, i) => {
                      const state: SaveState = p.email
                        ? saved[p.email] ?? "idle"
                        : "idle";
                      return (
                        <tr key={`${p.name}-${i}`} className="hover:bg-base-200/40">
                          <td className="font-medium whitespace-nowrap">
                            {p.name}
                          </td>
                          <td className="text-sm opacity-80">
                            {p.title || "—"}
                          </td>
                          <td>
                            {p.email ? (
                              <a
                                href={`mailto:${p.email}`}
                                className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                              >
                                <Mail className="h-3.5 w-3.5 shrink-0" />
                                {p.email}
                              </a>
                            ) : (
                              <span className="text-xs opacity-35">
                                no email found
                              </span>
                            )}
                          </td>
                          <td>
                            <ConfidenceBadge value={p.email ? p.score : null} />
                          </td>
                          <td>
                            {p.method ? (
                              <span className="rounded-full bg-base-300/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide opacity-60 whitespace-nowrap">
                                {METHOD_LABEL[p.method] ?? p.method}
                              </span>
                            ) : (
                              <span className="text-xs opacity-30">—</span>
                            )}
                          </td>
                          <td className="text-right">
                            {!p.email ? null : state === "saved" ? (
                              <span className="inline-flex items-center gap-1 text-xs text-success font-medium">
                                <Check className="h-3.5 w-3.5" strokeWidth={3} />
                                Saved
                              </span>
                            ) : state === "exists" ? (
                              <span className="inline-flex items-center gap-1 text-xs opacity-55">
                                <Check className="h-3.5 w-3.5" />
                                Already a lead
                              </span>
                            ) : state === "saving" ? (
                              <span className="inline-flex items-center gap-1 text-xs opacity-55">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                Saving…
                              </span>
                            ) : (
                              <button
                                type="button"
                                onClick={() => persist(p)}
                                className="btn btn-ghost btn-xs"
                              >
                                Save
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}
