import Link from "next/link";
import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import ApplicationsTable from "./ApplicationsTable";

export const dynamic = "force-dynamic";

const STATUSES = [
  "all",
  "Applied",
  "In-Progress",
  "Offer",
  "Rejected",
  "Withdrawn",
  "Ghosted",
] as const;
type StatusFilter = (typeof STATUSES)[number];

export default async function ApplicationsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const sp = await searchParams;
  const status: StatusFilter = (STATUSES as readonly string[]).includes(
    sp.status ?? "all"
  )
    ? (sp.status as StatusFilter)
    : "all";

  const [apps, resumes] = await Promise.all([
    prisma.jobApplication.findMany({
      where: status === "all" ? {} : { status },
      orderBy: { createdAt: "desc" },
      include: { resume: { select: { id: true, label: true } } },
    }),
    prisma.resume.findMany({
      where: { isActive: true },
      orderBy: { id: "asc" },
      select: { id: true, label: true },
    }),
  ]);

  const total = apps.length;

  const serialised = apps.map((a) => ({
    id: a.id,
    companyName: a.companyName,
    jobRole: a.jobRole,
    jobUrl: a.jobUrl,
    location: a.location,
    interviewStatus: a.interviewStatus,
    status: a.status,
    appliedDate: a.appliedDate.toISOString(),
    resumeId: a.resumeId,
    resumeLabel: a.resume?.label ?? null,
    companyCareerPage: a.companyCareerPage,
    decisionDate: a.decisionDate?.toISOString() ?? null,
    decisionTime: a.decisionTime,
    notes: a.notes,
    hrName: a.hrName,
    hrLinkedin: a.hrLinkedin,
    hrEmail: a.hrEmail,
    referral: a.referral,
    referralLinkedin: a.referralLinkedin,
  }));

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold tracking-tight">Applications</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {total}
          </span>
        </div>
        <div role="tablist" className="tabs tabs-bordered flex-wrap">
          {STATUSES.map((s) => (
            <Link
              key={s}
              role="tab"
              href={
                s === "all"
                  ? "/applications"
                  : `/applications?status=${encodeURIComponent(s)}`
              }
              className={`tab text-xs transition-colors ${
                s === status
                  ? "tab-active text-primary font-medium"
                  : "opacity-70 hover:opacity-100"
              }`}
            >
              {s === "all" ? "All" : s}
            </Link>
          ))}
        </div>
      </div>

      <ApplicationsTable apps={serialised} resumes={resumes} total={total} />
    </div>
  );
}
