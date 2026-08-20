import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import StatusStrip from "./StatusStrip";
import ScheduleNote from "./ScheduleNote";
import { SCHEDULE_KEY, type Schedule } from "./constants";
import ScheduleSettings from "./ScheduleSettings";
import GlobalSettings from "./GlobalSettings";
import RolesFeed from "./RolesFeed";

export const dynamic = "force-dynamic";

export default async function JobBoardPage({
  searchParams,
}: {
  searchParams: Promise<{ role?: string; company?: string }>;
}) {
  const sp = await searchParams;

  // Live postings only. Archived rows (the role came off the board) are kept
  // for history but never shown in the feed.
  const where = {
    archivedAt: null,
    ...(sp.role ? { matchedRole: sp.role } : {}),
    ...(sp.company ? { watchedCompanyId: sp.company } : {}),
  };

  const [postings, companies, roleGroups, retention, maxYearsSetting, countriesSetting, scheduleSetting] =
    await Promise.all([
    prisma.jobPosting.findMany({
      where,
      include: { watchedCompany: true },
      orderBy: [{ isNew: "desc" }, { firstSeenAt: "desc" }],
      take: 1000,
    }),
    prisma.watchedCompany.findMany({
      orderBy: [{ active: "desc" }, { name: "asc" }],
      include: { _count: { select: { postings: true } } },
    }),
    prisma.jobPosting.groupBy({
      by: ["matchedRole"],
      where: { archivedAt: null },
      _count: { matchedRole: true },
    }),
    prisma.setting.findUnique({ where: { key: "jobboard.retentionWeeks" } }),
    prisma.setting.findUnique({ where: { key: "jobboard.maxYears" } }),
    prisma.setting.findUnique({ where: { key: "jobboard.countries" } }),
    prisma.setting.findUnique({ where: { key: SCHEDULE_KEY } }),
  ]);

  const schedule: Schedule = {
    timezone: "America/New_York",
    monitorIntervalH: 3,
    monitorStart: "07:00",
    monitorEnd: "23:00",
    monitorDays: "*",
    alertAt: "09:00,18:00",
    alertDays: "*",
    // A stored row overrides the defaults field by field, so a partial save
    // (only alert times, say) keeps the rest rather than blanking it.
    ...(() => {
      try {
        return scheduleSetting?.value ? JSON.parse(scheduleSetting.value) : {};
      } catch {
        return {};
      }
    })(),
  };

  const retentionWeeks = Number(retention?.value ?? 4) || 4;
  const maxYears = Number(maxYearsSetting?.value ?? 0) || 0;
  const globalCountries = (countriesSetting?.value ?? "")
    .split(",")
    .map((c) => c.trim().toUpperCase())
    .filter((c) => c.length === 2);

  const serialisedPostings = postings.map((p) => ({
    id: p.id,
    title: p.title,
    matchedRole: p.matchedRole,
    location: p.location,
    url: p.url,
    postedAt: p.postedAt?.toISOString() ?? null,
    minYears: p.minYears,
    country: p.country,
    firstSeenAt: p.firstSeenAt.toISOString(),
    isNew: p.isNew,
    jobApplicationId: p.jobApplicationId,
    companyId: p.watchedCompany.id,
    companyName: p.watchedCompany.name,
    companyDomain: p.watchedCompany.domain,
    companyLogoUrl: p.watchedCompany.logoUrl,
  }));

  const serialisedCompanies = companies.map((c) => ({
    id: c.id,
    name: c.name,
    careerUrl: c.careerUrl,
    domain: c.domain,
    logoUrl: c.logoUrl,
    ats: c.ats,
    atsSlug: c.atsSlug,
    active: c.active,
    seededAt: c.seededAt?.toISOString() ?? null,
    lastCheckedAt: c.lastCheckedAt?.toISOString() ?? null,
    lastStatus: c.lastStatus,
    lastError: c.lastError,
    postingCount: c._count.postings,
    roleFilter: Array.isArray(c.roleFilter) ? (c.roleFilter as string[]) : null,
    countryFilter: Array.isArray(c.countryFilter) ? (c.countryFilter as string[]) : null,
  }));


  return (
    <div className="w-full px-4 py-6 sm:px-6 lg:px-8">
      <RefreshOnFocus />

      <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
        <h1 className="text-2xl font-semibold tracking-tight">Job Board</h1>
        <ScheduleNote
          pageCount={serialisedCompanies.filter((c) => c.active).length}
        />
        </div>
        <div className="flex flex-wrap items-center gap-2">
        <StatusStrip />
        <ScheduleSettings initial={schedule} />
        <GlobalSettings
          retentionWeeks={retentionWeeks}
          maxYears={maxYears}
          countries={globalCountries}
          liveCount={serialisedPostings.length}
        />
        </div>
      </header>

      <RolesFeed
        postings={serialisedPostings}
        companies={serialisedCompanies}
        roles={roleGroups.map((g) => ({
          role: g.matchedRole,
          count: g._count.matchedRole,
        }))}
        activeRole={sp.role ?? null}
        activeCompany={sp.company ?? null}
      />
    </div>
  );
}
