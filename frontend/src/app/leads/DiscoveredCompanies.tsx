"use client";

import { Fragment, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  Search,
  ExternalLink,
  Link2,
  Mail,
  ArrowUpDown,
  Globe,
  Building2,
  Trash2,
  Users,
  Info,
} from "lucide-react";
import { relativeTime } from "@/lib/time";
import HuntPanel from "./HuntPanel";
import ConfidenceBadge, { confidenceTier } from "./ConfidenceBadge";
import FitBadge from "./FitBadge";
import ContactBadge from "./ContactBadge";
import CompanyRoster from "./CompanyRoster";

// Number of <th> columns in the table — the roster/detail panels span all of them.
const COLUMN_COUNT = 12;

export type CompanyLead = {
  id: string;
  domain: string | null;
  companyName: string | null;
  fundingStage: string | null;
  fundingAmount: string | null;
  founderName: string | null;
  email: string | null;
  linkedinUrl: string | null;
  employeeCount: string | null;
  revenue: string | null;
  location: string | null;
  industry: string | null;
  lastRoundDate: string | null;
  confidence: number | null;
  source: string | null;
  sources: string[];
  // Deep-research fields — null/empty on rows hunted before the fit-gate upgrade.
  brief: string | null;
  foundingYear: string | null;
  totalRaised: string | null;
  investors: string[];
  competitors: string[];
  keyPeople: string[];
  fitScore: number | null;
  fitReason: string | null;
  updatedAt: string;
};

type SortKey = "confidence" | "fit" | "company" | "recent";
type ConfFilter = "all" | "high" | "medium" | "low";

const SOURCE_LABEL: Record<string, string> = {
  open_web: "Open web",
  yc_oss: "Y Combinator",
  product_hunt: "Product Hunt",
  rss: "Funding RSS",
  "agent-server": "Agent",
};

export default function DiscoveredCompanies({
  companies,
}: {
  companies: CompanyLead[];
}) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortKey>("confidence");
  const [conf, setConf] = useState<ConfFilter>("all");
  // Optimistic removal: hide deleted ids immediately, reconcile on refresh.
  const [removed, setRemoved] = useState<Set<string>>(new Set());
  // Which company's roster panel is expanded (one at a time). The panel finds
  // people at the company and saves each as a lead.
  const [rosterId, setRosterId] = useState<string | null>(null);
  // Which company's deep-research detail panel is expanded (one at a time).
  const [detailId, setDetailId] = useState<string | null>(null);

  function toggleRoster(id: string) {
    setRosterId((cur) => (cur === id ? null : id));
  }

  function toggleDetail(id: string) {
    setDetailId((cur) => (cur === id ? null : id));
  }

  function hide(id: string, on: boolean) {
    setRemoved((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  // Gmail-style delete: hide the row immediately and show an Undo toast. The
  // actual DELETE fires only after the toast's grace window, so Undo can cancel
  // it with no network round-trip — no jarring browser confirm() popup.
  function deleteCompany(c: CompanyLead) {
    const label = c.companyName ?? c.domain ?? "company";
    hide(c.id, true);
    let undone = false;

    const timer = setTimeout(async () => {
      if (undone) return;
      try {
        const res = await fetch(`/api/proxy/leads/${c.id}`, { method: "DELETE" });
        if (!res.ok) {
          const d = await res.json().catch(() => ({}));
          throw new Error(d.detail || `HTTP ${res.status}`);
        }
        // Remember the domain as dropped so future hunts skip it (best-effort).
        if (c.domain) {
          fetch(`/api/agent/api/v1/seen/drop`, {
            method: "POST",
            headers: { "content-type": "application/json" },
            body: JSON.stringify({ domain: c.domain, reason: "user_deleted" }),
          }).catch(() => {});
        }
        router.refresh();
      } catch (e) {
        hide(c.id, false); // restore on failure
        toast.error(`Could not delete ${label}: ${(e as Error).message}`);
      }
    }, 4500);

    toast(`Deleted ${label}`, {
      description: "Won't be surfaced by future hunts.",
      duration: 4500,
      action: {
        label: "Undo",
        onClick: () => {
          undone = true;
          clearTimeout(timer);
          hide(c.id, false);
        },
      },
    });
  }

  const filtered = useMemo(() => {
    let rows = companies.filter((c) => !removed.has(c.id));

    if (conf !== "all") {
      rows = rows.filter(
        (c) => c.confidence != null && confidenceTier(c.confidence) === conf
      );
    }

    if (query.trim()) {
      const q = query.toLowerCase();
      rows = rows.filter(
        (c) =>
          (c.companyName ?? "").toLowerCase().includes(q) ||
          (c.domain ?? "").toLowerCase().includes(q) ||
          (c.founderName ?? "").toLowerCase().includes(q) ||
          (c.fundingStage ?? "").toLowerCase().includes(q) ||
          (c.industry ?? "").toLowerCase().includes(q) ||
          (c.location ?? "").toLowerCase().includes(q)
      );
    }

    const sorted = [...rows];
    if (sort === "confidence") {
      sorted.sort((a, b) => (b.confidence ?? -1) - (a.confidence ?? -1));
    } else if (sort === "fit") {
      sorted.sort((a, b) => (b.fitScore ?? -1) - (a.fitScore ?? -1));
    } else if (sort === "company") {
      sorted.sort((a, b) =>
        (a.companyName ?? a.domain ?? "").localeCompare(
          b.companyName ?? b.domain ?? ""
        )
      );
    } else {
      sorted.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    }
    return sorted;
  }, [companies, query, sort, conf, removed]);

  return (
    <div className="space-y-5">
      <HuntPanel />

      {/* Controls */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex items-center gap-2 rounded-xl border border-base-300/60 bg-base-200/40 px-3.5 h-10 transition-colors focus-within:border-primary/60 focus-within:bg-base-200/70 hover:border-base-300 flex-1 min-w-[240px]">
          <Search className="pointer-events-none h-4 w-4 opacity-50 shrink-0" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search company, domain, founder, stage…"
            className="flex-1 bg-transparent border-0 outline-none text-sm placeholder:opacity-40"
          />
          {query && (
            <button
              onClick={() => setQuery("")}
              className="text-xs opacity-50 hover:opacity-100 shrink-0"
              aria-label="Clear search"
            >
              ✕
            </button>
          )}
        </div>

        {/* Confidence filter */}
        <div
          role="tablist"
          className="inline-flex items-center gap-0.5 p-1 rounded-lg bg-base-200/60 border border-base-300/60"
        >
          {(["all", "high", "medium", "low"] as ConfFilter[]).map((f) => (
            <button
              key={f}
              role="tab"
              aria-selected={conf === f}
              onClick={() => setConf(f)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize ${
                conf === f
                  ? "bg-base-100 text-base-content shadow-sm ring-1 ring-base-300/80"
                  : "text-base-content/60 hover:text-base-content hover:bg-base-300/40"
              }`}
            >
              {f}
            </button>
          ))}
        </div>

        {/* Sort */}
        <div className="flex items-center gap-2 rounded-lg border border-base-300/60 bg-base-200/40 px-3 h-10">
          <ArrowUpDown className="h-3.5 w-3.5 opacity-50" />
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="bg-transparent border-0 outline-none text-sm pr-1"
          >
            <option value="confidence">Confidence</option>
            <option value="fit">Best fit</option>
            <option value="company">Company A–Z</option>
            <option value="recent">Most recent</option>
          </select>
        </div>
      </div>

      {/* Table / empty state */}
      {filtered.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-base-300/70 bg-base-200/20 p-10 text-center">
          <Building2 className="h-7 w-7 mx-auto opacity-30" />
          <p className="mt-3 text-sm opacity-60">
            {companies.length === 0
              ? "No discovered companies yet. Start a hunt above to find some."
              : "No companies match your filters."}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-2xl border border-base-300/60">
          <table className="table">
            <thead>
              <tr className="text-xs uppercase tracking-wide opacity-60">
                <th>Company</th>
                <th>Funding</th>
                <th>Industry</th>
                <th>Employees</th>
                <th>Location</th>
                <th>Founder</th>
                <th>Confidence</th>
                <th>Fit</th>
                <th>Contact</th>
                <th>Source</th>
                <th className="text-right">Found</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <Fragment key={c.id}>
                <tr className="hover:bg-base-200/40 transition-colors">
                  <td>
                    <div className="flex items-center gap-2.5">
                      <span className="grid place-items-center h-8 w-8 rounded-lg bg-base-200 text-base-content/50 shrink-0">
                        <Globe className="h-4 w-4" />
                      </span>
                      <div className="min-w-0">
                        <div className="font-medium truncate">
                          {c.companyName ?? c.domain}
                        </div>
                        {c.domain && (
                          <a
                            href={`https://${c.domain}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs opacity-50 hover:opacity-90 hover:text-primary inline-flex items-center gap-1 truncate"
                          >
                            {c.domain}
                            <ExternalLink className="h-3 w-3 shrink-0" />
                          </a>
                        )}
                      </div>
                    </div>
                  </td>
                  <td>
                    {c.fundingStage || c.fundingAmount ? (
                      <div className="flex flex-col gap-0.5">
                        {c.fundingStage && (
                          <span className="badge badge-ghost badge-sm">
                            {c.fundingStage}
                          </span>
                        )}
                        {c.fundingAmount && (
                          <span className="text-xs opacity-60 tabular-nums">
                            {c.fundingAmount}
                          </span>
                        )}
                        {c.lastRoundDate && (
                          <span className="text-[11px] opacity-40">
                            {c.lastRoundDate}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs opacity-30">—</span>
                    )}
                  </td>
                  <td>
                    {c.industry ? (
                      <span className="badge badge-ghost badge-sm whitespace-nowrap">
                        {c.industry}
                      </span>
                    ) : (
                      <span className="text-xs opacity-30">—</span>
                    )}
                  </td>
                  <td>
                    <span className="text-xs tabular-nums opacity-70">
                      {c.employeeCount || "—"}
                    </span>
                  </td>
                  <td>
                    <span className="text-xs opacity-70 whitespace-nowrap">
                      {c.revenue ? (
                        <span title={`Revenue: ${c.revenue}`}>
                          {c.location || "—"}
                          <span className="block text-[11px] opacity-50">
                            {c.revenue}
                          </span>
                        </span>
                      ) : (
                        c.location || "—"
                      )}
                    </span>
                  </td>
                  <td>
                    {c.founderName ? (
                      <span className="text-sm">{c.founderName}</span>
                    ) : (
                      <span className="text-xs opacity-30">—</span>
                    )}
                  </td>
                  <td>
                    <ConfidenceBadge value={c.confidence} showLabel />
                  </td>
                  <td>
                    <FitBadge value={c.fitScore} reason={c.fitReason} />
                  </td>
                  <td>
                    <ContactBadge email={c.email} linkedinUrl={c.linkedinUrl} />
                  </td>
                  <td>
                    <span className="text-xs opacity-60">
                      {c.source ? (SOURCE_LABEL[c.source] ?? c.source) : "—"}
                    </span>
                  </td>
                  <td className="text-right">
                    <span className="text-xs opacity-50 whitespace-nowrap">
                      {relativeTime(new Date(c.updatedAt))}
                    </span>
                  </td>
                  <td>
                    <div className="flex items-center justify-end gap-1">
                      {(c.brief ||
                        c.foundingYear ||
                        c.totalRaised ||
                        c.investors.length > 0 ||
                        c.competitors.length > 0 ||
                        c.keyPeople.length > 0 ||
                        c.fitReason) && (
                        <button
                          type="button"
                          onClick={() => toggleDetail(c.id)}
                          className={`btn btn-ghost btn-xs btn-square ${
                            detailId === c.id
                              ? "bg-primary/10 text-primary"
                              : ""
                          }`}
                          title="Deep-research brief & details"
                          aria-label="Show research details"
                          aria-expanded={detailId === c.id}
                        >
                          <Info className="h-3.5 w-3.5" />
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => toggleRoster(c.id)}
                        className={`btn btn-ghost btn-xs btn-square ${
                          rosterId === c.id
                            ? "bg-primary/10 text-primary"
                            : ""
                        }`}
                        title="Find people — recruiters & eng leadership"
                        aria-label="Find people at this company"
                        aria-expanded={rosterId === c.id}
                      >
                        <Users className="h-3.5 w-3.5" />
                      </button>
                      {c.linkedinUrl && (
                        <a
                          href={c.linkedinUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="btn btn-ghost btn-xs btn-square"
                          title="Founder LinkedIn"
                        >
                          <Link2 className="h-3.5 w-3.5" />
                        </a>
                      )}
                      {c.email && (
                        <a
                          href={`mailto:${c.email}`}
                          className="btn btn-ghost btn-xs btn-square"
                          title={c.email}
                        >
                          <Mail className="h-3.5 w-3.5" />
                        </a>
                      )}
                      <button
                        type="button"
                        onClick={() => deleteCompany(c)}
                        className="btn btn-ghost btn-xs btn-square text-error/70 hover:text-error hover:bg-error/10"
                        title="Delete — won't be re-surfaced by future hunts"
                        aria-label="Delete company"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </td>
                </tr>
                {detailId === c.id && (
                  <tr className="bg-base-200/30">
                    <td colSpan={COLUMN_COUNT} className="p-0">
                      <div className="px-5 py-4 space-y-3 border-t border-base-300/50">
                        {/* Fit reasoning from the gate */}
                        {c.fitReason && (
                          <p className="text-xs opacity-70 leading-relaxed">
                            <span className="font-medium opacity-90">
                              Why it fits:
                            </span>{" "}
                            {c.fitReason}
                          </p>
                        )}
                        {/* Qualitative deep-research brief */}
                        {c.brief && (
                          <p className="text-sm opacity-80 leading-relaxed whitespace-pre-line max-w-3xl">
                            {c.brief}
                          </p>
                        )}
                        {/* Structured research facts (each null-guarded) */}
                        <div className="flex flex-wrap gap-x-8 gap-y-2 text-xs">
                          {c.foundingYear && (
                            <div>
                              <span className="opacity-50">Founded</span>
                              <div className="opacity-80 tabular-nums">
                                {c.foundingYear}
                              </div>
                            </div>
                          )}
                          {c.totalRaised && (
                            <div>
                              <span className="opacity-50">Total raised</span>
                              <div className="opacity-80 tabular-nums">
                                {c.totalRaised}
                              </div>
                            </div>
                          )}
                          {c.investors.length > 0 && (
                            <div className="min-w-0">
                              <span className="opacity-50">Investors</span>
                              <div className="opacity-80">
                                {c.investors.join(", ")}
                              </div>
                            </div>
                          )}
                          {c.competitors.length > 0 && (
                            <div className="min-w-0">
                              <span className="opacity-50">Competitors</span>
                              <div className="opacity-80">
                                {c.competitors.join(", ")}
                              </div>
                            </div>
                          )}
                          {c.keyPeople.length > 0 && (
                            <div className="min-w-0">
                              <span className="opacity-50">Key people</span>
                              <div className="opacity-80">
                                {c.keyPeople.join(", ")}
                              </div>
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
                {rosterId === c.id && (
                  <CompanyRoster
                    domain={c.domain}
                    company={c.companyName ?? c.domain}
                    colSpan={COLUMN_COUNT}
                  />
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="text-xs opacity-50">
        Discovered by the lead-generation agent — domain-deduplicated, funding
        &amp; founder researched, and confidence-scored. Confidence reflects how
        well the founder email / LinkedIn could be verified.
      </p>
    </div>
  );
}
