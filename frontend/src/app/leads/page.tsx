import Link from "next/link";
import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import LeadsTable from "./LeadsTable";

export const dynamic = "force-dynamic";

const FILTERS = ["all", "pending", "replied"] as const;
type LeadFilter = (typeof FILTERS)[number];

export default async function LeadsPage({
  searchParams,
}: {
  searchParams: Promise<{ filter?: string }>;
}) {
  const sp = await searchParams;
  const filter: LeadFilter = (FILTERS as readonly string[]).includes(
    sp.filter ?? "all"
  )
    ? (sp.filter as LeadFilter)
    : "all";

  const where =
    filter === "replied"
      ? { replied: true }
      : filter === "pending"
        ? { replied: false }
        : {};

  const leads = await prisma.lead.findMany({
    where,
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

  const total = leads.length;

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

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Leads</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {total}
          </span>
        </div>
        <div
          role="tablist"
          className="inline-flex items-center gap-0.5 p-1 rounded-lg bg-base-200/60 border border-base-300/60 flex-wrap"
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

      <p className="text-sm opacity-60">
        People you can reach out to. Each lead is auto-linked to any Reach
        Out emails sent from this app, so you can see outreach history at a
        glance.
      </p>

      <LeadsTable leads={serialised} total={total} />
    </div>
  );
}
