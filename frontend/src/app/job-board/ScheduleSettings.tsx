"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { CalendarClock, X, RefreshCw, Mail, Globe2 } from "lucide-react";
import { setSchedule } from "./actions";
import { type Schedule } from "./constants";

/**
 * When to scan, and when to alert.
 *
 * Separate from Scrape settings because these answer a different question:
 * Scrape settings decide WHAT is kept, this decides WHEN the work happens.
 *
 * Narrowing anything here cannot lose roles — the alert window is a watermark
 * over the last SENT alert, so a role found outside alert hours is reported by
 * the next alert instead of being dropped. That is why there is no warning
 * about missing postings, and why saving needs no confirmation.
 */

const DAY_PRESETS = [
  { value: "*", label: "Every day" },
  { value: "mon-fri", label: "Weekdays" },
  { value: "sat-sun", label: "Weekends" },
];

// A short list beats a full IANA picker here: these cover the realistic cases,
// and anything else can still be set via JOBBOARD_TIMEZONE.
const TZ_OPTIONS = [
  "America/New_York",
  "America/Chicago",
  "America/Denver",
  "America/Los_Angeles",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Kolkata",
  "Asia/Singapore",
  "Australia/Sydney",
  "UTC",
];

const HOURS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, "0")}:00`);

export default function ScheduleSettings({ initial }: { initial: Schedule }) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<Schedule>(initial);

  function patch(next: Partial<Schedule>) {
    setForm((f) => ({ ...f, ...next }));
  }

  async function save(next: Schedule) {
    setBusy(true);
    try {
      const res = await setSchedule(next);
      if (res.error) toast.error(res.error);
      else toast.success(res.message ?? "Saved.");
      startTransition(() => router.refresh());
    } finally {
      setBusy(false);
    }
  }

  // Alert times are edited as a chip set rather than free text: the whole point
  // is picking a couple of hours, and a text field invites the typos that would
  // silently mute the alert.
  const alertTimes = form.alertAt.split(",").map((t) => t.trim()).filter(Boolean);
  function toggleTime(time: string) {
    const next = alertTimes.includes(time)
      ? alertTimes.filter((t) => t !== time)
      : [...alertTimes, time].sort();
    patch({ alertAt: next.join(",") });
  }

  const windowed = form.monitorStart !== form.monitorEnd;
  const summary = `${form.monitorIntervalH}h · ${
    alertTimes.length ? alertTimes.join(", ") : "interval"
  }`;

  return (
    <div className="relative">
      <button
        className="inline-flex h-7 items-center gap-1.5 rounded-md border border-base-300 bg-base-100 px-2.5 text-xs transition-colors hover:border-primary"
        onClick={() => setOpen(!open)}
        title="When scanning and alerting happen"
      >
        <CalendarClock className="h-3.5 w-3.5 opacity-60" />
        Schedule
        <span className="tabular-nums opacity-50">{summary}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute right-0 z-20 mt-2 w-96 rounded-box border border-base-300 bg-base-100 p-4 shadow-lg">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold">Schedule</h3>
              <button
                className="btn btn-ghost btn-xs btn-square"
                onClick={() => setOpen(false)}
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>

            <p className="mb-4 text-xs opacity-60">
              Nothing is missed by narrowing these — roles found outside alert
              hours are reported by the next alert.
            </p>

            <div className="space-y-4">
              {/* ── Scan ─────────────────────────────────────────────── */}
              <div>
                <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                  <RefreshCw className="h-3.5 w-3.5" />
                  Scan career pages
                </span>
                <div className="flex items-center gap-2">
                  <select
                    className="h-8 flex-1 rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                    value={String(form.monitorIntervalH)}
                    onChange={(e) => patch({ monitorIntervalH: Number(e.target.value) })}
                    disabled={busy}
                  >
                    {[1, 2, 3, 4, 6, 8, 12, 24].map((h) => (
                      <option key={h} value={h}>
                        Every {h}h
                      </option>
                    ))}
                  </select>
                  <select
                    className="h-8 flex-1 rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                    value={form.monitorDays}
                    onChange={(e) => patch({ monitorDays: e.target.value })}
                    disabled={busy}
                  >
                    {DAY_PRESETS.map((d) => (
                      <option key={d.value} value={d.value}>
                        {d.label}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="mt-2 flex items-center gap-2">
                  <select
                    className="h-8 flex-1 rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                    value={form.monitorStart}
                    onChange={(e) => patch({ monitorStart: e.target.value })}
                    disabled={busy}
                  >
                    {HOURS.map((h) => (
                      <option key={h} value={h}>
                        From {h}
                      </option>
                    ))}
                  </select>
                  <select
                    className="h-8 flex-1 rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                    value={form.monitorEnd}
                    onChange={(e) => patch({ monitorEnd: e.target.value })}
                    disabled={busy}
                  >
                    {HOURS.map((h) => (
                      <option key={h} value={h}>
                        To {h}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="mt-1.5 text-xs opacity-50">
                  {windowed
                    ? `Only between ${form.monitorStart} and ${form.monitorEnd}.`
                    : "Around the clock (start and end are the same)."}
                </p>
              </div>

              {/* ── Alert ────────────────────────────────────────────── */}
              <div>
                <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                  <Mail className="h-3.5 w-3.5" />
                  Email an alert at
                </span>
                {/* Every hour, not every other one: a half-hour grid would hide
                    an already-selected odd hour (09:00), making it impossible to
                    see or clear what is actually set. */}
                <div className="grid grid-cols-6 gap-1">
                  {HOURS.map((h) => (
                    <button
                      key={h}
                      onClick={() => toggleTime(h)}
                      disabled={busy}
                      className={`rounded-md px-2 py-1 text-xs tabular-nums transition-colors ${
                        alertTimes.includes(h)
                          ? "bg-primary text-primary-content"
                          : "bg-base-200 hover:bg-base-300"
                      }`}
                    >
                      {h}
                    </button>
                  ))}
                </div>
                <select
                  className="mt-2 h-8 w-full rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                  value={form.alertDays}
                  onChange={(e) => patch({ alertDays: e.target.value })}
                  disabled={busy}
                >
                  {DAY_PRESETS.map((d) => (
                    <option key={d.value} value={d.value}>
                      {d.label}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs opacity-50">
                  {alertTimes.length
                    ? `${alertTimes.length} ${alertTimes.length === 1 ? "email" : "emails"} a day.`
                    : "No times picked — falls back to a fixed interval."}
                </p>
              </div>

              {/* ── Timezone ─────────────────────────────────────────── */}
              <label className="block">
                <span className="mb-1.5 flex items-center gap-1.5 text-xs font-medium opacity-70">
                  <Globe2 className="h-3.5 w-3.5" />
                  Times are in
                </span>
                <select
                  className="h-8 w-full rounded-md border border-base-300 bg-base-100 px-2 text-sm outline-none focus:border-primary"
                  value={form.timezone}
                  onChange={(e) => patch({ timezone: e.target.value })}
                  disabled={busy}
                >
                  {TZ_OPTIONS.map((tz) => (
                    <option key={tz} value={tz}>
                      {tz.replace(/_/g, " ")}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <div className="mt-4 flex items-center justify-end gap-2 border-t border-base-300/50 pt-3">
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setForm(initial)}
                disabled={busy}
              >
                Reset
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={() => save(form)}
                disabled={busy}
              >
                {busy ? "Saving…" : "Save schedule"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
