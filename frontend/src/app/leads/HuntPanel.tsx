"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Radar, Loader2, Square } from "lucide-react";

type JobStatus = {
  job_id: string;
  status: "pending" | "running" | "succeeded" | "failed" | "stopped";
  verified_count: number;
  target_count: number;
  candidates_total: number | null;
  candidates_processed: number;
  stop_reason: string | null;
};

const TERMINAL = new Set(["succeeded", "failed", "stopped"]);

export default function HuntPanel() {
  const router = useRouter();
  const [target, setTarget] = useState(10);
  const [hint, setHint] = useState("");
  const [job, setJob] = useState<JobStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const lastVerified = useRef(0);

  // Poll the agent server while a job is in flight; refresh the table as new
  // verified leads land, and stop polling on a terminal state.
  useEffect(() => {
    if (!job || TERMINAL.has(job.status)) return;
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/agent/api/v1/hunt/${job.job_id}`, {
          cache: "no-store",
        });
        if (!res.ok) return;
        const next: JobStatus = await res.json();
        setJob(next);
        // New leads were delivered — pull the fresh rows into the table.
        if (next.verified_count > lastVerified.current) {
          lastVerified.current = next.verified_count;
          router.refresh();
        }
        if (TERMINAL.has(next.status)) {
          router.refresh();
          if (next.status === "succeeded") {
            toast.success(
              `Hunt complete — ${next.verified_count} verified leads.`
            );
          } else if (next.status === "failed") {
            toast.error("Hunt failed. Check the agent server logs.");
          } else {
            toast(`Hunt ${next.status} (${next.stop_reason ?? ""}).`);
          }
        }
      } catch {
        /* transient; keep polling */
      }
    }, 2500);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [job, router]);

  async function startHunt() {
    if (starting) return;
    setStarting(true);
    lastVerified.current = 0;
    try {
      const res = await fetch(`/api/agent/api/v1/hunt`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          target_count: target,
          query_hint: hint.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setJob({
        job_id: data.job_id,
        status: data.status ?? "pending",
        verified_count: 0,
        target_count: target,
        candidates_total: null,
        candidates_processed: 0,
        stop_reason: null,
      });
      toast.success("Hunt started — discovering companies…");
    } catch (e) {
      toast.error(`Could not start hunt: ${(e as Error).message}`);
    } finally {
      setStarting(false);
    }
  }

  const active = job && !TERMINAL.has(job.status);
  const pct =
    job && job.target_count > 0
      ? Math.min(100, Math.round((job.verified_count / job.target_count) * 100))
      : 0;

  return (
    <div className="card border border-base-300/60 bg-base-200/30 rounded-2xl">
      <div className="card-body gap-4 p-5">
        <div className="flex items-center gap-2.5">
          <span className="grid place-items-center h-8 w-8 rounded-lg bg-primary/10 text-primary">
            <Radar className="h-4 w-4" />
          </span>
          <div>
            <h2 className="font-medium leading-tight">Discover companies</h2>
            <p className="text-xs opacity-55">
              The agent reads the open web + free startup sources, researches
              funding &amp; founders, then delivers verified leads here.
            </p>
          </div>
        </div>

        <div className="flex items-end gap-3 flex-wrap">
          <label className="form-control">
            <span className="label-text text-xs opacity-60 mb-1">
              Target leads
            </span>
            <input
              type="number"
              min={1}
              max={50}
              value={target}
              disabled={!!active}
              onChange={(e) =>
                setTarget(
                  Math.max(1, Math.min(50, Number(e.target.value) || 1))
                )
              }
              className="input input-bordered input-sm w-28 tabular-nums"
            />
          </label>
          <label className="form-control flex-1 min-w-[240px]">
            <span className="label-text text-xs opacity-60 mb-1">
              Focus (optional)
            </span>
            <input
              type="text"
              value={hint}
              disabled={!!active}
              onChange={(e) => setHint(e.target.value)}
              placeholder="e.g. recently funded AI dev-tools startups"
              className="input input-bordered input-sm w-full"
            />
          </label>
          <button
            type="button"
            onClick={startHunt}
            disabled={starting || !!active}
            className="btn btn-primary btn-sm gap-2"
          >
            {starting || active ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Radar className="h-4 w-4" />
            )}
            {active ? "Hunting…" : "Start hunt"}
          </button>
        </div>

        {job && (
          <div className="space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="flex items-center gap-2">
                {active ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
                ) : job.status === "succeeded" ? (
                  <span className="text-success">✓</span>
                ) : (
                  <Square className="h-3 w-3" />
                )}
                <span className="font-medium capitalize">{job.status}</span>
                {job.candidates_total != null && (
                  <span className="opacity-50">
                    · {job.candidates_processed}/{job.candidates_total}{" "}
                    candidates
                  </span>
                )}
                {job.stop_reason && (
                  <span className="opacity-40">· {job.stop_reason}</span>
                )}
              </span>
              <span className="font-mono tabular-nums opacity-70">
                {job.verified_count}/{job.target_count} verified
              </span>
            </div>
            <progress
              className="progress progress-primary w-full h-2"
              value={pct}
              max={100}
            />
          </div>
        )}
      </div>
    </div>
  );
}
