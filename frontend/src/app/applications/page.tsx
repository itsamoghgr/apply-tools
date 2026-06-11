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

// Newest-first window we render by default. With ~800 apps in the DB
// pulling everything every navigation made the page sluggish; this caps
// the initial payload to a usable chunk. `?all=1` opts back into full mode.
const PAGE_SIZE = 200;

export default async function ApplicationsPage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; all?: string; q?: string }>;
}) {
  const sp = await searchParams;
  const status: StatusFilter = (STATUSES as readonly string[]).includes(
    sp.status ?? "all"
  )
    ? (sp.status as StatusFilter)
    : "all";
  const showAll = sp.all === "1";
  const query = (sp.q ?? "").trim();

  // Search runs in the DB so it covers *all* applications, not just the
  // PAGE_SIZE window that's loaded for browsing. Match the same fields the
  // table used to filter client-side. When a query is present we also lift
  // the 200-cap so every match is returned (a query is its own bound).
  const searchWhere = query
    ? {
        OR: [
          { companyName: { contains: query, mode: "insensitive" as const } },
          { jobRole: { contains: query, mode: "insensitive" as const } },
          { location: { contains: query, mode: "insensitive" as const } },
          { status: { contains: query, mode: "insensitive" as const } },
          { interviewStatus: { contains: query, mode: "insensitive" as const } },
          { notes: { contains: query, mode: "insensitive" as const } },
          { hrName: { contains: query, mode: "insensitive" as const } },
          { referral: { contains: query, mode: "insensitive" as const } },
          { jobDescription: { contains: query, mode: "insensitive" as const } },
        ],
      }
    : {};

  const statusWhere = status === "all" ? {} : { status };
  const where =
    query && status !== "all"
      ? { AND: [statusWhere, searchWhere] }
      : query
        ? searchWhere
        : statusWhere;

  // Once searching, return all matches regardless of the browse cap.
  const limited = !showAll && !query;

  const [apps, resumes, total, orderedIds] = await Promise.all([
    prisma.jobApplication.findMany({
      where,
      // Sort by appliedDate so the user-controlled date drives the row's
      // position. createdAt is a tie-breaker for rows applied on the same
      // day (newest-saved first).
      orderBy: [{ appliedDate: "desc" }, { createdAt: "desc" }],
      take: limited ? PAGE_SIZE : undefined,
      include: {
        resume: { select: { id: true, label: true } },
        leads: {
          include: {
            lead: {
              select: {
                id: true,
                name: true,
                email: true,
                linkedinUrl: true,
                // linkedinProfile intentionally omitted: it's a raw scraped
                // profile blob (often tens of KB) that the table never
                // renders. Pulling it for every linked lead on every page
                // load was a hidden tax.
                currentCompany: true,
                role: true,
              },
            },
          },
          orderBy: { createdAt: "asc" },
        },
        reachOuts: {
          orderBy: { createdAt: "desc" },
          // Cap the reach-out preview list per app — we only surface the
          // most recent few in the row; the rest live on the Reach-out page.
          take: 5,
          select: {
            id: true,
            subject: true,
            status: true,
            sentAt: true,
            recipientName: true,
            recipientEmail: true,
            createdAt: true,
          },
        },
      },
    }),
    prisma.resume.findMany({
      where: { isActive: true },
      orderBy: { id: "asc" },
      select: { id: true, label: true },
    }),
    // Header badge / "X of Y" denominator: count the status scope, not the
    // search-narrowed set, so the number reflects the dataset being searched.
    prisma.jobApplication.count({ where: statusWhere }),
    // Full status-scoped ordering (ids only) so each row gets a TRUE Sl No
    // ranked against every application — stable whether the browse view is
    // capped at PAGE_SIZE, fully loaded, or narrowed by a non-contiguous
    // search. Same sort key as the rows so ranks line up with row order.
    prisma.jobApplication.findMany({
      where: statusWhere,
      orderBy: [{ appliedDate: "desc" }, { createdAt: "desc" }],
      select: { id: true },
    }),
  ]);

  // Newest application is #total; each subsequent row in the desc ordering is
  // one less. Build id -> Sl No once for O(1) lookup in the table.
  const slNoById: Record<string, number> = {};
  orderedIds.forEach((row, idx) => {
    slNoById[row.id] = total - idx;
  });

  // Only the browse window can be truncated; a search returns all its matches.
  const truncated = limited && total > apps.length;

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
    jobDescription: a.jobDescription,
    linkedLeads: a.leads.map((jl) => ({
      id: jl.lead.id,
      name: jl.lead.name,
      email: jl.lead.email,
      linkedinUrl: jl.lead.linkedinUrl,
      // Field kept on the row type for compatibility with downstream
      // consumers, but never selected on this page. Always null here.
      linkedinProfile: null as string | null,
      currentCompany: jl.lead.currentCompany,
      role: jl.lead.role,
      linkRole: jl.role,
    })),
    reachOuts: a.reachOuts.map((r) => ({
      id: r.id,
      subject: r.subject,
      status: r.status,
      sentAt: r.sentAt?.toISOString() ?? null,
      recipientName: r.recipientName,
      recipientEmail: r.recipientEmail,
      createdAt: r.createdAt.toISOString(),
    })),
  }));

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Applications</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {total}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <a
            href={
              status === "all"
                ? "/api/applications/export"
                : `/api/applications/export?status=${encodeURIComponent(status)}`
            }
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium border border-base-300/60 bg-base-200/40 hover:bg-base-200/70 transition-colors"
            download
          >
            <svg
              className="h-3.5 w-3.5 opacity-70"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3"
              />
            </svg>
            Export CSV
          </a>
          <div
            role="tablist"
            className="inline-flex items-center gap-0.5 p-1 rounded-lg bg-base-200/60 border border-base-300/60 flex-wrap"
          >
          {STATUSES.map((s) => {
            const active = s === status;
            return (
              <Link
                key={s}
                role="tab"
                aria-selected={active}
                href={
                  s === "all"
                    ? "/applications"
                    : `/applications?status=${encodeURIComponent(s)}`
                }
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                  active
                    ? "bg-base-100 text-base-content shadow-sm ring-1 ring-base-300/80"
                    : "text-base-content/60 hover:text-base-content hover:bg-base-300/40"
                }`}
              >
                {s === "all" ? "All" : s}
              </Link>
            );
          })}
          </div>
        </div>
      </div>

      <ApplicationsTable
        apps={serialised}
        resumes={resumes}
        total={total}
        slNoById={slNoById}
        query={query}
        status={status}
      />

      {truncated && (
        <div className="flex items-center justify-center pt-2">
          <Link
            href={
              status === "all"
                ? "/applications?all=1"
                : `/applications?status=${encodeURIComponent(status)}&all=1`
            }
            className="text-xs px-3 py-1.5 rounded-md border border-base-300/60 bg-base-200/40 hover:bg-base-200/70 transition-colors opacity-70 hover:opacity-100"
          >
            Showing {apps.length} of {total} · load all
          </Link>
        </div>
      )}
    </div>
  );
}
