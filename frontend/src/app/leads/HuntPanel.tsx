"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Radar, Loader2, Square, Terminal } from "lucide-react";

type JobStatus = {
  status: "pending" | "running" | "succeeded" | "failed" | "stopped";
  verified_count: number | null;
  skipped_count: number | null;
  target_count: number | null;
  candidates_total: number | null;
  candidates_processed: number | null;
  stop_reason: string | null;
};

type Activity = {
  id: number;
  stage: string;
  event: string;
  domain: string | null;
  data: Record<string, unknown> | null;
};

const TERMINAL = new Set(["succeeded", "failed", "stopped"]);

// Turn a raw audit event into a short human line for the live log.
function describe(a: Activity): string | null {
  const d = a.data ?? {};
  const dom = a.domain ? ` ${a.domain}` : "";
  switch (`${a.stage}.${a.event}`) {
    case "discovery.start":
      return d.query_hint
        ? `Discovering: “${d.query_hint}”`
        : "Discovering companies…";
    case "discovery.floor_fetched":
      return `Pulled ${d.total_floor ?? 0} from free sources (YC ${d.yc ?? 0}, RSS ${d.rss ?? 0}, PH ${d.product_hunt ?? d.producthunt ?? 0})`;
    case "discovery.open_web_start":
      return "Reading the open web…";
    case "discovery.tool_called":
      return d.tool === "web_search"
        ? "🔎 Searching the web…"
        : "📄 Reading a page…";
    case "discovery.open_web_cap":
      return `Open-web reading done (${d.tool_calls_used ?? "?"} tool calls)`;
    case "discovery.open_web_error":
      return "Open-web reading hit a snag — using free sources";
    case "discovery.done":
      return `Found ${d.total ?? d.raw_count ?? "?"} candidate companies`;
    case "dedup.done":
      return `Deduplicated → ${d.survivors ?? "?"} to research`;
    case "fit.skipped":
      return `↳${dom}: skipped — low fit`;
    case "fit.passed":
      return `↳${dom}: passed fit gate`;
    case "loop.iteration_start":
      return `Researching${dom}…`;
    case "research.shortcut_taken":
    case "research.shortcut":
      return `↳${dom}: funding from a structured source`;
    case "research.llm_call":
      return `↳${dom}: thinking…`;
    case "research.llm_final":
      return `↳${dom}: research complete`;
    case "research.done":
      return d.founder
        ? `↳${dom}: founder ${d.founder}`
        : `↳${dom}: researched`;
    case "research.fatal_error":
      return `↳${dom}: research error (skipped)`;
    case "verify.done":
      return `↳${dom}: verified (confidence ${Math.round(Number(d.confidence ?? 0) * 100)}%)`;
    case "deliver.outbox_queued":
      return `↳${dom}: queued for delivery`;
    case "deliver.delivered":
    case "deliver.sent":
      return `✓ Delivered${dom}`;
    case "deliver.delivery_failed":
      return `↳${dom}: delivery failed (will retry)`;
    default:
      return null; // noisy/internal events are hidden
  }
}

export default function HuntPanel() {
  const router = useRouter();
  const [target, setTarget] = useState(10);
  const [hint, setHint] = useState("");
  const [fitCriteria, setFitCriteria] = useState("");
  const [job, setJob] = useState<JobStatus | null>(null);
  const [log, setLog] = useState<{ id: number; text: string }[]>([]);
  const [starting, setStarting] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const lastVerified = useRef(0);
  const logEndRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll the activity log to the newest line.
  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [log]);

  // Clean up the SSE connection on unmount.
  useEffect(() => () => esRef.current?.close(), []);

  function openStream(jobId: string) {
    esRef.current?.close();
    const es = new EventSource(`/api/agent/api/v1/hunt/${jobId}/events`);
    esRef.current = es;

    es.addEventListener("activity", (e) => {
      try {
        const a: Activity = JSON.parse((e as MessageEvent).data);
        const text = describe(a);
        if (text) setLog((prev) => [...prev, { id: a.id, text }]);
      } catch {
        /* ignore malformed line */
      }
    });

    es.addEventListener("status", (e) => {
      try {
        const s: JobStatus = JSON.parse((e as MessageEvent).data);
        setJob(s);
        if ((s.verified_count ?? 0) > lastVerified.current) {
          lastVerified.current = s.verified_count ?? 0;
          router.refresh(); // pull newly delivered leads into the table
        }
      } catch {
        /* ignore */
      }
    });

    const finish = (e: Event) => {
      try {
        const s: JobStatus = JSON.parse((e as MessageEvent).data);
        setJob(s);
        router.refresh();
        if (s.status === "succeeded")
          toast.success(`Hunt complete — ${s.verified_count} verified leads.`);
        else if (s.status === "failed")
          toast.error("Hunt failed. Check the agent server logs.");
        else toast(`Hunt ${s.status} (${s.stop_reason ?? ""}).`);
      } catch {
        /* ignore */
      }
      es.close();
    };
    es.addEventListener("done", finish);
    es.onerror = () => {
      // Network blip or stream closed; close so the browser doesn't hammer it.
      es.close();
    };
  }

  async function startHunt() {
    if (starting) return;
    setStarting(true);
    lastVerified.current = 0;
    setLog([]);
    try {
      const res = await fetch(`/api/agent/api/v1/hunt`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          target_count: target,
          query_hint: hint.trim() || undefined,
          fit_criteria: fitCriteria.trim() || undefined,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      setJob({
        status: data.status ?? "pending",
        verified_count: 0,
        skipped_count: 0,
        target_count: target,
        candidates_total: null,
        candidates_processed: 0,
        stop_reason: null,
      });
      openStream(data.job_id);
      toast.success("Hunt started — discovering companies…");
    } catch (e) {
      toast.error(`Could not start hunt: ${(e as Error).message}`);
    } finally {
      setStarting(false);
    }
  }

  const active = job && !TERMINAL.has(job.status);
  const pct =
    job && job.target_count
      ? Math.min(
          100,
          Math.round(((job.verified_count ?? 0) / job.target_count) * 100)
        )
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

        {/* ICP / fit criteria — companies that don't match are skipped before
            the (expensive) deep research, so this gates spend. */}
        <label className="form-control">
          <span className="label-text text-xs opacity-60 mb-1">
            Who&apos;s a good lead for you? (ICP)
          </span>
          <textarea
            value={fitCriteria}
            disabled={!!active}
            onChange={(e) => setFitCriteria(e.target.value)}
            rows={2}
            placeholder="e.g. seed–Series A B2B SaaS in the US, 5–50 people, hiring engineers"
            className="textarea textarea-bordered textarea-sm w-full resize-y"
          />
          <span className="label-text-alt text-[11px] opacity-45 mt-1">
            Leave blank to skip fit filtering.
          </span>
        </label>

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
                {(job.skipped_count ?? 0) > 0 && (
                  <span className="opacity-50"> · {job.skipped_count} skipped</span>
                )}
              </span>
            </div>
            <progress
              className="progress progress-primary w-full h-2"
              value={pct}
              max={100}
            />

            {/* Live activity log streamed via SSE */}
            {log.length > 0 && (
              <div className="mt-1 rounded-lg border border-base-300/50 bg-base-300/20">
                <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-base-300/40 text-[11px] uppercase tracking-wide opacity-50">
                  <Terminal className="h-3 w-3" />
                  Live activity
                </div>
                <div className="max-h-44 overflow-y-auto px-3 py-2 space-y-0.5 font-mono text-xs leading-relaxed">
                  {log.slice(-80).map((l) => (
                    <div key={l.id} className="opacity-80">
                      {l.text}
                    </div>
                  ))}
                  <div ref={logEndRef} />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
