"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { RefreshCw, Mail, Clock } from "lucide-react";

type Run = {
  status: string;
  trigger: string;
  postings_new: number | null;
  companies_total: number | null;
  finished_at: string | null;
  started_at: string;
};

type Status = {
  enabled: boolean;
  monitor_interval_h: number;
  digest_interval_h: number;
  jobs: { id: string; next_run_at: string | null }[];
  last_monitor: Run | null;
  last_digest: Run | null;
};

function relative(iso: string | null): string {
  if (!iso) return "never";
  const delta = Date.now() - new Date(iso).getTime();
  if (delta < 0) {
    const mins = Math.round(-delta / 60000);
    return mins < 60 ? `in ${mins}m` : `in ${Math.round(mins / 60)}h`;
  }
  const mins = Math.round(delta / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

export default function StatusStrip() {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [status, setStatus] = useState<Status | null>(null);
  const [busy, setBusy] = useState<"monitor" | "digest" | null>(null);

  async function load(signal?: AbortSignal) {
    try {
      const res = await fetch("/api/agent/api/v1/jobboard/status", {
        cache: "no-store",
        signal,
      });
      if (signal?.aborted) return;
      setStatus(res.ok ? await res.json() : null);
    } catch {
      if (!signal?.aborted) setStatus(null); // agent down — show offline state
    }
  }

  useEffect(() => {
    // Fetch-on-mount from an external service; the abort guard keeps a slow or
    // failed request from setting state after unmount. Same disable as
    // leads/CompanyRoster.tsx and components/ThemeToggle.tsx.
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load(controller.signal);
    return () => controller.abort();
  }, []);

  async function trigger(kind: "monitor" | "digest") {
    setBusy(kind);
    try {
      const path =
        kind === "monitor"
          ? "/api/agent/api/v1/jobboard/monitor/run"
          : "/api/agent/api/v1/jobboard/digest/send";
      const res = await fetch(path, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(kind === "digest" ? { force: false } : {}),
      });
      if (!res.ok) throw new Error(await res.text());
      toast.success(
        kind === "monitor"
          ? "Monitor cycle started — refresh in a moment."
          : "Digest queued.",
      );
      // Give the background task a beat before re-reading status + postings.
      setTimeout(() => {
        load();
        startTransition(() => router.refresh());
      }, 4000);
    } catch {
      toast.error(`Could not start the ${kind}. Is the agent service running?`);
    } finally {
      setBusy(null);
    }
  }

  const nextMonitor =
    status?.jobs.find((j) => j.id === "jobboard_monitor")?.next_run_at ?? null;

  // Compact header control rather than a full-width bar: four short facts and two
  // actions never justified a bordered box of their own. Timing detail moves
  // into the title attribute, where it is one hover away.
  const offline = status === null;
  const failed = status?.last_monitor?.status === "failed";
  const scanned = relative(status?.last_monitor?.finished_at ?? null);
  const detail = offline
    ? "Agent service offline — schedules paused."
    : [
        `Last scan ${scanned}`,
        `Next ${relative(nextMonitor)}`,
        `Last digest ${relative(status?.last_digest?.finished_at ?? null)}`,
      ].join(" · ");

  return (
    <div className="flex items-center gap-1.5" title={detail}>
      <span
        className={`flex items-center gap-1.5 text-xs ${
          offline || failed ? "text-error" : "opacity-60"
        }`}
      >
        <Clock className="h-3.5 w-3.5" />
        {offline ? "offline" : failed ? "scan failed" : `scanned ${scanned}`}
      </span>

      <button
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-base-300 bg-base-100 px-2.5 text-xs transition-colors hover:border-primary disabled:opacity-50"
        onClick={() => trigger("monitor")}
        disabled={busy !== null}
        title="Scrape every watched career page now"
      >
        <RefreshCw
          className={`h-3.5 w-3.5 opacity-60 ${busy === "monitor" ? "animate-spin" : ""}`}
        />
        Scan
      </button>
      <button
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-base-300 bg-base-100 px-2.5 text-xs transition-colors hover:border-primary disabled:opacity-50"
        onClick={() => trigger("digest")}
        disabled={busy !== null}
        title="Send the digest email now"
      >
        <Mail className="h-3.5 w-3.5 opacity-60" />
        Digest
      </button>
    </div>
  );
}
