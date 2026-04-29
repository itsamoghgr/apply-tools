import Link from "next/link";
import { prisma } from "@/lib/prisma";
import {
  Send,
  CalendarCheck,
  Briefcase,
  MessageSquare,
  Clock,
  TrendingUp,
  TrendingDown,
  Minus,
} from "lucide-react";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import Sparkline from "@/components/Sparkline";
import { relativeTime } from "@/lib/time";

export const dynamic = "force-dynamic";

// appliedDate values come from a `<input type="date">` (a local calendar
// date) and are stored as midnight in SQLite without a timezone, which
// Prisma surfaces as midnight UTC. Compare against UTC midnight of the
// user's *local* day so the boundaries line up with how the data was saved.
const APP_TZ = "America/New_York";

function startOfLocalTodayAsUTC(tz: string = APP_TZ): Date {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const get = (t: string) =>
    Number(parts.find((p) => p.type === t)?.value ?? "0");
  return new Date(Date.UTC(get("year"), get("month") - 1, get("day")));
}

function startOfLocalWeekAsUTC(tz: string = APP_TZ): Date {
  const today = startOfLocalTodayAsUTC(tz);
  const dow = today.getUTCDay();
  const daysSinceMonday = (dow + 6) % 7;
  const monday = new Date(today);
  monday.setUTCDate(monday.getUTCDate() - daysSinceMonday);
  return monday;
}

function addUTCDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setUTCDate(out.getUTCDate() + n);
  return out;
}

const ACTIVE_STATUSES = ["Applied", "In-Progress"];

const MODE_COLORS: Record<string, string> = {
  cover_letter: "mode-dot-cover_letter",
  cover_letter_text: "mode-dot-cover_letter",
  email: "mode-dot-email",
  outreach: "mode-dot-outreach",
  score: "mode-dot-score",
  answer_question: "mode-dot-answer_question",
};

export default async function Home() {
  const todayStart = startOfLocalTodayAsUTC();
  const yesterdayStart = addUTCDays(todayStart, -1);
  const weekStart = startOfLocalWeekAsUTC();
  const lastWeekStart = addUTCDays(weekStart, -7);
  const thirtyDaysAgo = addUTCDays(todayStart, -29); // inclusive 30-day window

  const [
    recent,
    totalApplications,
    appliedToday,
    appliedYesterday,
    appliedThisWeek,
    appliedLastWeek,
    activeApplications,
    interviewCount,
    last30Rows,
  ] = await Promise.all([
    prisma.application.findMany({
      orderBy: { createdAt: "desc" },
      take: 8,
      select: {
        id: true,
        mode: true,
        company: true,
        createdAt: true,
        resume: { select: { label: true } },
      },
    }),
    prisma.jobApplication.count(),
    prisma.jobApplication.count({
      where: { appliedDate: { gte: todayStart } },
    }),
    prisma.jobApplication.count({
      where: { appliedDate: { gte: yesterdayStart, lt: todayStart } },
    }),
    prisma.jobApplication.count({
      where: { appliedDate: { gte: weekStart } },
    }),
    prisma.jobApplication.count({
      where: { appliedDate: { gte: lastWeekStart, lt: weekStart } },
    }),
    prisma.jobApplication.count({
      where: { status: { in: ACTIVE_STATUSES } },
    }),
    prisma.jobApplication.count({
      where: { interviewStatus: { not: null } },
    }),
    prisma.jobApplication.findMany({
      where: { appliedDate: { gte: thirtyDaysAgo } },
      select: { appliedDate: true },
      orderBy: { appliedDate: "asc" },
    }),
  ]);

  // Bucket the 30-day rows into per-day counts (oldest → newest).
  const dayBuckets: number[] = Array.from({ length: 30 }, () => 0);
  for (const r of last30Rows) {
    const dayKey = Math.floor(
      (r.appliedDate.getTime() - thirtyDaysAgo.getTime()) / 86_400_000
    );
    if (dayKey >= 0 && dayKey < 30) dayBuckets[dayKey] += 1;
  }
  const peakDay = Math.max(...dayBuckets, 0);
  const last30Total = dayBuckets.reduce((a, b) => a + b, 0);

  // Group recent activity by relative-day label for readability.
  const now = new Date();
  const grouped: Record<string, typeof recent> = {};
  for (const a of recent) {
    const ageHr = (now.getTime() - a.createdAt.getTime()) / 3_600_000;
    const key =
      ageHr < 24 ? "Today" : ageHr < 48 ? "Yesterday" : "Earlier";
    (grouped[key] ||= []).push(a);
  }
  const groupOrder = ["Today", "Yesterday", "Earlier"].filter((k) => grouped[k]);

  return (
    <div className="space-y-10">
      <RefreshOnFocus />

      {/* ─── Header ─── */}
      <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-6 animate-slide-up stagger-1">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Overview</h1>
          <p className="text-sm opacity-50 mt-1">
            Application activity at a glance.
          </p>
        </div>

        <div className="glass-card p-5 w-full lg:w-[360px] shrink-0">
          <div className="flex items-center justify-between mb-2">
            <div>
              <div className="text-[10px] uppercase tracking-widest opacity-50 font-semibold">
                Last 30 days
              </div>
              <div className="flex items-baseline gap-2 mt-1">
                <span className="text-2xl font-bold tabular-nums">
                  {last30Total}
                </span>
                <span className="text-xs opacity-50">
                  applications · peak {peakDay}/day
                </span>
              </div>
            </div>
          </div>
          <div className="h-16">
            <Sparkline values={dayBuckets} />
          </div>
        </div>
      </div>

      {/* ─── Application Stats ─── */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        <StatCard
          href="/applications"
          label="Total"
          value={totalApplications}
          icon={<Briefcase className="h-4 w-4" />}
          tint="primary"
          stagger="stagger-2"
        />
        <StatCard
          href="/applications"
          label="Applied today"
          value={appliedToday}
          delta={diff(appliedToday, appliedYesterday)}
          deltaLabel="vs yesterday"
          icon={<Send className="h-4 w-4" />}
          tint="success"
          accent
          stagger="stagger-2"
        />
        <StatCard
          href="/applications"
          label="This week"
          value={appliedThisWeek}
          delta={diff(appliedThisWeek, appliedLastWeek)}
          deltaLabel="vs last week"
          icon={<CalendarCheck className="h-4 w-4" />}
          tint="info"
          stagger="stagger-3"
        />
        <StatCard
          href="/applications?status=In-Progress"
          label="Active"
          value={activeApplications}
          icon={<Clock className="h-4 w-4" />}
          tint="warning"
          stagger="stagger-3"
        />
        <StatCard
          href="/applications"
          label="Interviews"
          value={interviewCount}
          icon={<MessageSquare className="h-4 w-4" />}
          tint="accent"
          className="col-span-2 sm:col-span-1"
          stagger="stagger-4"
        />
      </div>

      {/* ─── Recent Activity ─── */}
      <div className="animate-slide-up stagger-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xs font-semibold uppercase tracking-widest opacity-50">
            Recent Activity
          </h2>
          <Link
            href="/history"
            className="text-sm text-primary hover:text-accent transition-colors group"
          >
            View all{" "}
            <span className="inline-block transition-transform group-hover:translate-x-1">
              →
            </span>
          </Link>
        </div>

        {recent.length === 0 ? (
          <div className="glass-card p-8 text-center">
            <div className="text-3xl mb-3">✨</div>
            <p className="text-sm opacity-60">
              Nothing here yet — generate something via the extension or any of
              the tools above.
            </p>
          </div>
        ) : (
          <div className="space-y-4">
            {groupOrder.map((label) => (
              <div key={label}>
                <div className="text-[10px] uppercase tracking-widest opacity-40 font-semibold mb-2 px-1">
                  {label}
                </div>
                <div className="glass-card overflow-hidden divide-y divide-base-300/40">
                  {grouped[label].map((a) => (
                    <div
                      key={a.id}
                      className="px-5 py-3 flex items-center justify-between gap-3 hover:bg-base-200/40 transition-colors"
                    >
                      <div className="flex items-center gap-3 min-w-0 flex-1">
                        <span
                          className={`mode-dot ${MODE_COLORS[a.mode] ?? "mode-dot-cover_letter"}`}
                        />
                        <span className="badge badge-ghost badge-sm font-mono text-[10px] uppercase tracking-wider shrink-0">
                          {a.mode.replace(/_/g, " ")}
                        </span>
                        <span className="font-medium truncate">
                          {a.company ?? "—"}
                        </span>
                        <span className="opacity-50 text-sm hidden sm:inline truncate">
                          {a.resume?.label ?? "—"}
                        </span>
                      </div>
                      <span
                        className="opacity-40 text-xs tabular-nums whitespace-nowrap"
                        title={a.createdAt.toLocaleString()}
                      >
                        {relativeTime(a.createdAt, now)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────────────

function diff(curr: number, prev: number): number {
  return curr - prev;
}

const TINT_CLASSES: Record<
  string,
  { bg: string; text: string; hover: string }
> = {
  primary: {
    bg: "bg-primary/10",
    text: "text-primary",
    hover: "group-hover:bg-primary/20",
  },
  success: {
    bg: "bg-success/10",
    text: "text-success",
    hover: "group-hover:bg-success/20",
  },
  info: {
    bg: "bg-info/10",
    text: "text-info",
    hover: "group-hover:bg-info/20",
  },
  warning: {
    bg: "bg-warning/10",
    text: "text-warning",
    hover: "group-hover:bg-warning/20",
  },
  accent: {
    bg: "bg-accent/10",
    text: "text-accent",
    hover: "group-hover:bg-accent/20",
  },
};

function StatCard({
  href,
  label,
  value,
  icon,
  tint,
  delta,
  deltaLabel,
  accent,
  stagger,
  className = "",
}: {
  href: string;
  label: string;
  value: number;
  icon: React.ReactNode;
  tint: keyof typeof TINT_CLASSES;
  delta?: number;
  deltaLabel?: string;
  accent?: boolean;
  stagger?: string;
  className?: string;
}) {
  const tintClass = TINT_CLASSES[tint];
  return (
    <Link
      href={href}
      className={`glass-card glass-card-lift p-4 group cursor-pointer animate-slide-up ${stagger ?? ""} ${className}`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div
            className={`text-2xl font-bold tracking-tight tabular-nums ${
              accent ? "gradient-text" : ""
            }`}
          >
            {value}
          </div>
          <div className="text-xs opacity-60 mt-1 font-medium truncate">
            {label}
          </div>
        </div>
        <div
          className={`p-1.5 rounded-lg transition-colors ${tintClass.bg} ${tintClass.text} ${tintClass.hover}`}
        >
          {icon}
        </div>
      </div>
      {typeof delta === "number" && (
        <div className="mt-2 flex items-center gap-1.5 text-[11px]">
          <DeltaPill value={delta} />
          <span className="opacity-40">{deltaLabel}</span>
        </div>
      )}
    </Link>
  );
}

function DeltaPill({ value }: { value: number }) {
  if (value === 0) {
    return (
      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded font-medium opacity-50">
        <Minus className="h-3 w-3" />
        flat
      </span>
    );
  }
  const positive = value > 0;
  return (
    <span
      className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded font-medium ${
        positive
          ? "text-success bg-success/10"
          : "text-error bg-error/10"
      }`}
    >
      {positive ? (
        <TrendingUp className="h-3 w-3" />
      ) : (
        <TrendingDown className="h-3 w-3" />
      )}
      {positive ? "+" : ""}
      {value}
    </span>
  );
}

