"use client";

import { useEffect, useState } from "react";

type Status = {
  timezone: string;
  monitor_interval_h: number;
  monitor_active_start: string;
  monitor_active_end: string;
  monitor_days: string;
  alert_interval_h: number;
  alert_at: string;
  alert_days: string;
};

/** "*" means every day and is better left unsaid; anything else ("mon-fri")
 *  already reads as English. */
function daysLabel(days: string): string {
  return !days || days === "*" ? "" : ` ${days}`;
}

/** The schedule as a sentence. Reads the agent's live config rather than
 *  restating a hardcoded interval, which silently went stale the moment the
 *  schedule became configurable. */
function summarise(s: Status): string {
  const windowed = s.monitor_active_start !== s.monitor_active_end;
  const scrape = windowed
    ? `every ${s.monitor_interval_h}h between ${s.monitor_active_start}–${s.monitor_active_end}${daysLabel(s.monitor_days)}`
    : `every ${s.monitor_interval_h}h${daysLabel(s.monitor_days)}`;
  const times = (s.alert_at || "").trim();
  const alert = times
    ? `Alerts at ${times.split(",").map((t) => t.trim()).join(", ")}${daysLabel(s.alert_days)}`
    : `Alerts every ${s.alert_interval_h}h`;
  return `Scanning ${scrape}. ${alert}.`;
}

export default function ScheduleNote({ pageCount }: { pageCount: number }) {
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    let alive = true;
    fetch("/api/agent/api/v1/jobboard/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && setStatus(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const pages = `Monitoring ${pageCount} career ${pageCount === 1 ? "page" : "pages"}.`;

  return (
    <p className="mt-1 text-sm opacity-70" title={status ? `Times in ${status.timezone}` : undefined}>
      {pages} {status ? summarise(status) : ""}
    </p>
  );
}
