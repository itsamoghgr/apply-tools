import Link from "next/link";
import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import LeadsTable from "./LeadsTable";
import DiscoveredCompanies, {
  type CompanyLead,
} from "./DiscoveredCompanies";

export const dynamic = "force-dynamic";

// Two views on one page: the existing person/outreach leads, and the
// company leads discovered by the lead-generation agent service.
const VIEWS = ["outreach", "discovered"] as const;
type LeadView = (typeof VIEWS)[number];

const FILTERS = ["all", "pending", "replied"] as const;
type LeadFilter = (typeof FILTERS)[number];

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string; view?: string }>;
}) {
  const sp = await searchParams;
  const view: LeadView = (VIEWS as readonly string[]).includes(sp.view ?? "")
    ? (sp.view as LeadView)
    : "outreach";
  const filter: LeadFilter = (FILTERS as readonly string[]).includes(
    sp.filter ?? "all"
  )
    ? (sp.filter as LeadFilter)
    : "all";

  // ── Outreach (person) leads: rows WITHOUT an agent-set domain ──────────────
  const where =
    filter === "replied"
      ? { replied: true }
      : filter === "pending"
        ? { replied: false }
        : {};

  const leads = await prisma.lead.findMany({
    where: { ...where, domain: null },
    orderBy: { createdAt: "desc" },
    include: {
      reachOuts: {
        select: {
          id: true,
          status: true,
          subject: true,
          sentAt: true,
          createdAt: true,
        },
        orderBy: { createdAt: "desc" },
      },
    },
  });

  const serialised = leads.map((l) => ({
    id: l.id,
    name: l.name,
    email: l.email,
    linkedinUrl: l.linkedinUrl,
    linkedinProfile: l.linkedinProfile,
    currentCompany: l.currentCompany,
    role: l.role,
    replied: l.replied,
    repliedAt: l.repliedAt?.toISOString() ?? null,
    notes: l.notes,
    createdAt: l.createdAt.toISOString(),
    updatedAt: l.updatedAt.toISOString(),
    reachOuts: l.reachOuts.map((r) => ({
      id: r.id,
      status: r.status,
      subject: r.subject,
      sentAt: r.sentAt?.toISOString() ?? null,
      createdAt: r.createdAt.toISOString(),
    })),
  }));

  // ── Discovered company leads: rows WITH an agent-set domain ────────────────
  const companyRows = await prisma.lead.findMany({
    where: { domain: { not: null } },
    orderBy: { updatedAt: "desc" },
  });

  const companies: CompanyLead[] = companyRows.map((l) => ({
    id: l.id,
    domain: l.domain,
    companyName: l.companyName ?? l.currentCompany,
    fundingStage: l.fundingStage,
    fundingAmount: l.fundingAmount,
    founderName: l.founderName ?? l.name,
    email: l.email,
    linkedinUrl: l.linkedinUrl,
    employeeCount: l.employeeCount,
    revenue: l.revenue,
    location: l.location,
    industry: l.industry,
    lastRoundDate: l.lastRoundDate,
    confidence: l.confidence,
    source: l.source,
    sources: Array.isArray(l.sourcesJson)
      ? (l.sourcesJson as unknown[]).map(String)
      : [],
    // Deep-research fields (older rows predate these → null/empty arrays).
    brief: l.brief,
    foundingYear: l.foundingYear,
    totalRaised: l.totalRaised,
    investors: Array.isArray(l.investorsJson)
      ? (l.investorsJson as unknown[]).map(String)
      : [],
    competitors: Array.isArray(l.competitorsJson)
      ? (l.competitorsJson as unknown[]).map(String)
      : [],
    keyPeople: Array.isArray(l.keyPeopleJson)
      ? (l.keyPeopleJson as unknown[]).map(String)
      : [],
    fitScore: l.fitScore,
    fitReason: l.fitReason,
    updatedAt: l.updatedAt.toISOString(),
  }));

  const outreachCount = serialised.length;
  const discoveredCount = companies.length;

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />

      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">
            Leads
          </h1>
        </div>

        {/* View switch: outreach people vs discovered companies */}
        <div
          role="tablist"
          className="inline-flex items-center gap-0.5 p-1 rounded-lg bg-base-200/60 border border-base-300/60 flex-wrap"
        >
          <Link
            role="tab"
            aria-selected={view === "outreach"}
            href="/leads"
            className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors flex items-center gap-1.5 ${
              view === "outreach"
                ? "bg-base-100 text-base-content shadow-sm ring-1 ring-base-300/80"
                : "text-base-content/60 hover:text-base-content hover:bg-base-300/40"
            }`}
          >
            People
            <span className="badge badge-xs badge-ghost font-mono tabular-nums">
              {outreachCount}
            </span>
          </Link>
          <Link
            role="tab"
            aria-selected={view === "discovered"}
            href="/leads?view=discovered"
            className={`px-3.5 py-1.5 rounded-md text-xs font-medium transition-colors flex items-center gap-1.5 ${
              view === "discovered"
                ? "bg-base-100 text-base-content shadow-sm ring-1 ring-base-300/80"
                : "text-base-content/60 hover:text-base-content hover:bg-base-300/40"
            }`}
          >
            Discovered
            <span className="badge badge-xs badge-ghost font-mono tabular-nums">
              {discoveredCount}
            </span>
          </Link>
        </div>
      </div>

      {view === "outreach" ? (
        <>
          {/* Sub-filter only applies to the outreach view */}
          <div className="flex items-center justify-between flex-wrap gap-3">
            <p className="text-sm opacity-60 max-w-2xl">
              People you can reach out to. Each lead is auto-linked to any Reach
              Out emails sent from this app, so you can see outreach history at a
              glance.
            </p>
            <div
              role="tablist"
              className="inline-flex items-center gap-0.5 p-1 rounded-lg bg-base-200/60 border border-base-300/60"
            >
              {FILTERS.map((f) => {
                const active = f === filter;
                return (
                  <Link
                    key={f}
                    role="tab"
                    aria-selected={active}
                    href={f === "all" ? "/leads" : `/leads?filter=${f}`}
                    className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize ${
                      active
                        ? "bg-base-100 text-base-content shadow-sm ring-1 ring-base-300/80"
                        : "text-base-content/60 hover:text-base-content hover:bg-base-300/40"
                    }`}
                  >
                    {f}
                  </Link>
                );
              })}
            </div>
          </div>
          <LeadsTable leads={serialised} total={outreachCount} />
        </>
      ) : (
        <DiscoveredCompanies companies={companies} />
      )}
    </div>
  );
}
