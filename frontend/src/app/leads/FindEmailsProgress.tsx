"use client";

import { useEffect, useRef } from "react";
import {
  MailSearch,
  Loader2,
  Check,
  Minus,
  Mail,
  Sparkles,
} from "lucide-react";

export type FindRow = {
  name: string;
  status: "pending" | "searching" | "found" | "miss";
  email?: string;
  method?: string;
};

export type FindProgress = {
  done: number;
  total: number;
  rows: FindRow[];
};

const METHOD_LABEL: Record<string, string> = {
  web_snippet: "open web",
  smtp: "verified (MX)",
  pattern: "pattern",
  apollo: "Apollo",
  hunter: "Hunter",
  abstract: "Abstract",
  agent: "agent",
};

export default function FindEmailsProgress({
  progress,
  finding,
}: {
  progress: FindProgress;
  finding: boolean;
}) {
  const { done, total, rows } = progress;
  const found = rows.filter((r) => r.status === "found").length;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const current = rows.find((r) => r.status === "searching");
  const activeRef = useRef<HTMLDivElement | null>(null);

  // Keep the in-progress lead scrolled into view.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [current?.name]);

  return (
    <div className="rounded-2xl border border-base-300/60 bg-gradient-to-b from-base-200/50 to-base-200/20 overflow-hidden animate-slide-up">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-base-300/40">
        <span
          className={`grid place-items-center h-9 w-9 rounded-xl shrink-0 ${
            finding
              ? "bg-primary/10 text-primary"
              : "bg-success/10 text-success"
          }`}
        >
          {finding ? (
            <MailSearch className="h-4.5 w-4.5" />
          ) : (
            <Sparkles className="h-4.5 w-4.5" />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium leading-tight flex items-center gap-2">
            {finding ? "Finding emails…" : "Done finding emails"}
            {found > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-success/12 text-success px-2 py-0.5 text-xs font-medium">
                <Mail className="h-3 w-3" />
                {found} found
              </span>
            )}
          </div>
          <div className="text-xs opacity-55 truncate">
            {finding && current
              ? `Researching ${current.name} — open web, patterns, then verify`
              : finding
                ? "The agent searches the open web first, then verifies"
                : `${found} of ${total} contacts now have an email`}
          </div>
        </div>
        <span className="text-sm font-mono tabular-nums opacity-70 shrink-0">
          {done}/{total}
        </span>
      </div>

      {/* Progress bar with percentage */}
      <div className="relative h-1.5 bg-base-300/40">
        <div
          className="absolute inset-y-0 left-0 bg-primary transition-[width] duration-500 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      {/* Per-lead rows */}
      <div className="max-h-56 overflow-y-auto px-2 py-1.5">
        {rows.map((r, i) => {
          const active = r.status === "searching";
          return (
            <div
              key={i}
              ref={active ? activeRef : undefined}
              className={`flex items-center gap-2.5 rounded-lg px-2 py-1.5 text-sm transition-colors ${
                active ? "bg-primary/5" : ""
              }`}
            >
              <span className="grid place-items-center h-5 w-5 shrink-0">
                {r.status === "found" ? (
                  <span className="grid place-items-center h-5 w-5 rounded-full bg-success/15 text-success">
                    <Check className="h-3 w-3" strokeWidth={3} />
                  </span>
                ) : r.status === "miss" ? (
                  <span className="grid place-items-center h-5 w-5 rounded-full bg-base-300/50 text-base-content/40">
                    <Minus className="h-3 w-3" />
                  </span>
                ) : r.status === "searching" ? (
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                ) : (
                  <span className="h-1.5 w-1.5 rounded-full bg-base-content/20" />
                )}
              </span>

              <span
                className={`font-medium truncate ${
                  r.status === "pending" ? "opacity-40" : ""
                } ${active ? "max-w-[160px]" : "max-w-[200px]"}`}
              >
                {r.name}
              </span>

              {r.status === "searching" && (
                <span className="text-xs text-primary/70 animate-pulse">
                  searching the web…
                </span>
              )}
              {r.status === "found" && (
                <span className="flex items-center gap-1.5 min-w-0">
                  <span className="text-success font-medium truncate">
                    {r.email}
                  </span>
                  {r.method && (
                    <span className="shrink-0 rounded-full bg-base-300/60 px-1.5 py-0.5 text-[10px] uppercase tracking-wide opacity-60">
                      {METHOD_LABEL[r.method] ?? r.method}
                    </span>
                  )}
                </span>
              )}
              {r.status === "miss" && (
                <span className="text-xs opacity-35">no email found</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
