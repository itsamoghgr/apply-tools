"use client";

import { useMemo, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import {
  ExternalLink, Plus, Check, EyeOff, Inbox, Search, X, MapPin,
  ChevronDown, ChevronRight, ListCollapse, SlidersHorizontal, RotateCw,
  Trash2, AlertCircle, Plus as PlusIcon,
} from "lucide-react";
import CompanyLogo from "./CompanyLogo";
import ScrapeConfig from "./ScrapeConfig";
import AddCompanyForm from "./AddCompanyForm";
import {
  trackPosting, dismissPostings, deleteWatchedCompany, redetectCompany,
  toggleWatchedCompany,
} from "./actions";

/** A watched company, with everything the header row needs to manage it. */
export type Company = {
  id: string;
  name: string;
  careerUrl: string;
  domain: string | null;
  logoUrl: string | null;
  ats: string | null;
  active: boolean;
  seededAt: string | null;
  lastStatus: string | null;
  lastError: string | null;
  roleFilter: string[] | null;
  countryFilter: string[] | null;
};

export type Posting = {
  id: string;
  title: string;
  matchedRole: string;
  location: string | null;
  url: string;
  postedAt: string | null;
  /** Minimum years of experience from the JD; null = the posting doesn't say. */
  minYears: number | null;
  /** ISO-3166 alpha-2 parsed from the location; null when unresolvable. */
  country: string | null;
  firstSeenAt: string;
  isNew: boolean;
  jobApplicationId: string | null;
  companyId: string;
  companyName: string;
  companyDomain: string | null;
  companyLogoUrl: string | null;
};

type SortKey = "newest" | "company" | "role" | "experience";

/** Mirrors EXPERIENCE_BANDS in agent_server/jobboard/experience.py. */
const EXPERIENCE_BANDS: { key: string; label: string; low: number; high: number | null }[] = [
  { key: "entry", label: "0-2 yrs", low: 0, high: 2 },
  { key: "mid", label: "3-5 yrs", low: 3, high: 5 },
  { key: "senior", label: "6-9 yrs", low: 6, high: 9 },
  { key: "staff", label: "10+ yrs", low: 10, high: null },
];

/** Display-only shortening: "Forward Deployed Engineer" wraps a fixed-width
 *  chip and makes its row taller than the rest. The full name is kept in the
 *  tooltip and everywhere else. */
const ROLE_SHORT: Record<string, string> = {
  "Forward Deployed Engineer": "Forward Deployed",
};

const ROLE_ORDER = [
  "Data Scientist",
  "Data Analyst",
  "AI Engineer",
  "Founding Engineer",
  "Forward Deployed Engineer",
];

function ageDays(posting: Posting): number {
  const iso = posting.postedAt ?? posting.firstSeenAt;
  return Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
}

function postedLabel(posting: Posting): string {
  const days = ageDays(posting);
  const prefix = posting.postedAt ? "posted" : "seen";
  if (days <= 0) return `${prefix} today`;
  if (days === 1) return `${prefix} yesterday`;
  if (days < 30) return `${prefix} ${days}d ago`;
  const iso = posting.postedAt ?? posting.firstSeenAt;
  return `${prefix} ${new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })}`;
}

/** Country/region prefix from Amazon-style "US, WA, Seattle" or "London, UK". */
/** Country names for the location filter, keyed by the parsed ISO code. */
const COUNTRY_LABELS: Record<string, string> = {
  US: "United States", CA: "Canada", GB: "United Kingdom", IE: "Ireland",
  DE: "Germany", FR: "France", ES: "Spain", PT: "Portugal", IT: "Italy",
  NL: "Netherlands", BE: "Belgium", CH: "Switzerland", AT: "Austria",
  SE: "Sweden", NO: "Norway", DK: "Denmark", FI: "Finland", PL: "Poland",
  CZ: "Czechia", RO: "Romania", RS: "Serbia", GR: "Greece", TR: "Turkey",
  IL: "Israel", IN: "India", CN: "China", JP: "Japan", KR: "South Korea",
  SG: "Singapore", AU: "Australia", NZ: "New Zealand", BR: "Brazil",
  MX: "Mexico", AR: "Argentina", AE: "UAE", ZA: "South Africa",
  HK: "Hong Kong", TW: "Taiwan", TH: "Thailand", VN: "Vietnam",
  PH: "Philippines", ID: "Indonesia", MY: "Malaysia", PK: "Pakistan",
  LT: "Lithuania", LU: "Luxembourg", UA: "Ukraine", HU: "Hungary",
};

/**
 * Group key for the location filter.
 *
 * Uses the PARSED country, which is consistent across every source. Grouping by
 * the raw location text mixed country codes with city names — "US (102)" beside
 * "San Francisco (10)" — because each board formats locations differently.
 */
function regionOf(posting: Posting): string | null {
  return posting.country ?? null;
}

export default function RolesFeed({
  postings,
  companies,
  roles,
  activeRole,
  activeCompany,
}: {
  postings: Posting[];
  companies: Company[];
  roles: { role: string; count: number }[];
  activeRole: string | null;
  activeCompany: string | null;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [newOnly, setNewOnly] = useState(false);
  const [hideTracked, setHideTracked] = useState(false);
  const [region, setRegion] = useState<string>("");
  const [maxAge, setMaxAge] = useState<string>("");
  const [sort, setSort] = useState<SortKey>("newest");
  const [expBand, setExpBand] = useState<string>("");
  const [configId, setConfigId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [companyBusy, setCompanyBusy] = useState<string | null>(null);
  // Holds COLLAPSED company ids (not expanded ones): a company that appears
  // after a scan is then open by default instead of silently hidden.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  function toggleCompany(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  // Region options come from the data, so they stay correct as companies change.
  // Labels are truncated for display: a multi-location posting can produce a
  // 117-character region string, and a native <select> sizes itself to its
  // widest option — one such row stretched this control across the toolbar.
  const regions = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of postings) {
      const r = regionOf(p);
      if (r) counts.set(r, (counts.get(r) ?? 0) + 1);
    }
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 24);
  }, [postings]);

  const regionLabel = (code: string) => COUNTRY_LABELS[code] ?? code;

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    const limit = maxAge ? Number(maxAge) : null;
    let list = postings.filter((p) => {
      if (newOnly && !p.isNew) return false;
      if (hideTracked && p.jobApplicationId) return false;
      if (region && regionOf(p) !== region) return false;
      if (limit !== null && ageDays(p) > limit) return false;
      if (expBand) {
        if (expBand === "unstated") {
          if (p.minYears !== null) return false;
        } else {
          // A posting with no stated requirement is EXCLUDED from a specific
          // band rather than assumed to fit it — guessing would surface roles
          // that may not match at all.
          if (p.minYears === null) return false;
          const band = EXPERIENCE_BANDS.find((b) => b.key === expBand);
          if (!band) return false;
          if (p.minYears < band.low) return false;
          if (band.high !== null && p.minYears > band.high) return false;
        }
      }
      if (q) {
        const hay = `${p.title} ${p.companyName} ${p.location ?? ""} ${p.matchedRole}`;
        if (!hay.toLowerCase().includes(q)) return false;
      }
      return true;
    });
    if (sort === "company") {
      list = [...list].sort(
        (a, b) =>
          a.companyName.localeCompare(b.companyName) ||
          a.title.localeCompare(b.title),
      );
    } else if (sort === "experience") {
      // Least experience first; unstated sorts last rather than as zero.
      list = [...list].sort(
        (a, b) => (a.minYears ?? 99) - (b.minYears ?? 99),
      );
    } else if (sort === "role") {
      list = [...list].sort(
        (a, b) =>
          ROLE_ORDER.indexOf(a.matchedRole) - ROLE_ORDER.indexOf(b.matchedRole) ||
          a.companyName.localeCompare(b.companyName),
      );
    }
    return list;
  }, [postings, query, newOnly, hideTracked, region, maxAge, expBand, sort]);

  // Group under the company. EVERY watched company gets an entry, including
  // ones with no matching roles — otherwise a company that matches nothing (or
  // whose scrape is failing) would vanish from the only page that manages it.
  const grouped = useMemo(() => {
    const map = new Map<string, Posting[]>();
    for (const company of companies) map.set(company.id, []);
    for (const posting of visible) {
      const list = map.get(posting.companyId) ?? [];
      list.push(posting);
      map.set(posting.companyId, list);
    }
    const byId = new Map(companies.map((c) => [c.id, c]));
    return [...map.entries()]
      .filter(([id]) => byId.has(id))
      // Companies with roles first; empty ones sink to the bottom.
      .sort((a, b) => (b[1].length > 0 ? 1 : 0) - (a[1].length > 0 ? 1 : 0));
  }, [visible, companies]);

  const anyFilter =
    Boolean(query || region || maxAge || expBand) || newOnly || hideTracked ||
    Boolean(activeRole) || Boolean(activeCompany);

  async function onTrack(posting: Posting) {
    setBusyId(posting.id);
    try {
      const res = await trackPosting(posting.id);
      if (res.error) toast.error(res.error);
      else toast.success(res.message ?? "Added to applications.");
      startTransition(() => router.refresh());
    } finally {
      setBusyId(null);
    }
  }

  async function onCompanyAction(
    id: string,
    fn: () => Promise<unknown>,
    successMessage?: string,
  ) {
    setCompanyBusy(id);
    try {
      const res = (await fn()) as { error?: string; message?: string } | undefined;
      if (res?.error) toast.error(res.error);
      else if (successMessage || res?.message) toast.success(res?.message ?? successMessage!);
      startTransition(() => router.refresh());
    } finally {
      setCompanyBusy(null);
    }
  }

  async function onDeleteCompany(company: Company, count: number) {
    if (
      !confirm(
        `Stop watching ${company.name}?\n\nIts ${count} stored posting(s) are ` +
          `removed too. Anything already pushed to Applications stays there.`,
      )
    )
      return;
    await onCompanyAction(
      company.id,
      () => deleteWatchedCompany(company.id),
      `Stopped watching ${company.name}.`,
    );
  }

  async function onDismissCompany(companyPostings: Posting[]) {
    const ids = companyPostings.filter((p) => p.isNew).map((p) => p.id);
    if (!ids.length) return;
    await dismissPostings(ids);
    toast.success(`Marked ${ids.length} as seen.`);
    startTransition(() => router.refresh());
  }

  function setParam(key: "role" | "company", value: string | null) {
    const params = new URLSearchParams();
    params.set("view", "roles");
    const role = key === "role" ? value : activeRole;
    const company = key === "company" ? value : activeCompany;
    if (role) params.set("role", role);
    if (company) params.set("company", company);
    router.push(`/job-board?${params}`);
  }

  function clearAll() {
    setQuery("");
    setRegion("");
    setMaxAge("");
    setExpBand("");
    setNewOnly(false);
    setHideTracked(false);
    router.push("/job-board?view=roles");
  }

  return (
    <>
      {/* ── Search + filters ───────────────────────────────────────────
          One bordered card instead of four loose rows. The search field is a
          plain input rather than DaisyUI's `input-bordered`, whose heavy border
          rendered as a black slab at this width. */}
      <div className="mb-4 overflow-hidden rounded-box border border-base-300">
        <div className="flex flex-wrap items-center gap-2 border-b border-base-300 bg-base-200/40 px-3 py-2">
          <div className="relative min-w-56 flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 opacity-40" />
            <input
              type="search"
              className="h-8 w-full rounded-md border border-base-300 bg-base-100 pl-8 pr-3 text-sm outline-none transition-colors placeholder:opacity-50 focus:border-primary"
              placeholder="Search title, company, or location…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>

          <div className="flex items-center gap-1.5">
            {roles.map((r) => {
              const on = activeRole === r.role;
              return (
                <button
                  key={r.role}
                  title={r.role}
                  className={`inline-flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                    on ? "bg-primary text-primary-content" : "bg-base-200 hover:bg-base-300"
                  }`}
                  onClick={() => setParam("role", on ? null : r.role)}
                >
                  {ROLE_SHORT[r.role] ?? r.role}
                  <span className={`tabular-nums ${on ? "opacity-80" : "opacity-45"}`}>
                    {r.count}
                  </span>
                </button>
              );
            })}
            {activeRole && (
              <button
                className="inline-flex h-8 items-center rounded-md px-2 text-xs opacity-60 hover:bg-base-200 hover:opacity-100"
                onClick={() => setParam("role", null)}
                title="Show all roles"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 px-3 py-2 text-xs">
          <select
            className="h-7 rounded-md border border-base-300 bg-base-100 px-2 text-xs outline-none focus:border-primary"
            value={activeCompany ?? ""}
            onChange={(e) => setParam("company", e.target.value || null)}
          >
            <option value="">All companies</option>
            {companies.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>

          <select
            className="h-7 max-w-44 truncate rounded-md border border-base-300 bg-base-100 px-2 text-xs outline-none focus:border-primary"
            value={region}
            onChange={(e) => setRegion(e.target.value)}
            title={region || "All locations"}
          >
            <option value="">All locations</option>
            {regions.map(([r, n]) => (
              <option key={r} value={r} title={regionLabel(r)}>
                {regionLabel(r)} ({n})
              </option>
            ))}
          </select>

          <select
            className="h-7 rounded-md border border-base-300 bg-base-100 px-2 text-xs outline-none focus:border-primary"
            value={expBand}
            onChange={(e) => setExpBand(e.target.value)}
            title="Minimum years of experience stated in the job description"
          >
            <option value="">Any experience</option>
            {EXPERIENCE_BANDS.map((b) => (
              <option key={b.key} value={b.key}>{b.label}</option>
            ))}
            <option value="unstated">Not stated</option>
          </select>

          <select
            className="h-7 rounded-md border border-base-300 bg-base-100 px-2 text-xs outline-none focus:border-primary"
            value={maxAge}
            onChange={(e) => setMaxAge(e.target.value)}
          >
            <option value="">Any age</option>
            <option value="0">Today</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
          </select>

          <select
            className="h-7 rounded-md border border-base-300 bg-base-100 px-2 text-xs outline-none focus:border-primary"
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
          >
            <option value="newest">Newest first</option>
            <option value="experience">Least experience</option>
            <option value="company">By company</option>
            <option value="role">By role</option>
          </select>

          <span className="mx-1 h-4 w-px bg-base-300" />

          <button
            className={`inline-flex h-7 items-center rounded-md px-2 font-medium transition-colors ${
              newOnly ? "bg-primary text-primary-content" : "hover:bg-base-200"
            }`}
            onClick={() => setNewOnly(!newOnly)}
          >
            New only
          </button>
          <button
            className={`inline-flex h-7 items-center rounded-md px-2 font-medium transition-colors ${
              hideTracked ? "bg-primary text-primary-content" : "hover:bg-base-200"
            }`}
            onClick={() => setHideTracked(!hideTracked)}
          >
            Hide applied
          </button>

          {anyFilter && (
            <button
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 opacity-60 hover:bg-base-200 hover:opacity-100"
              onClick={clearAll}
            >
              <X className="h-3 w-3" />
              Clear
            </button>
          )}

          <div className="ml-auto flex items-center gap-3">
            <button
              className="inline-flex h-7 items-center gap-1 rounded-md px-2 opacity-70 hover:bg-base-200 hover:opacity-100"
              onClick={() =>
                setCollapsed((prev) =>
                  prev.size ? new Set() : new Set(grouped.map(([id]) => id)),
                )
              }
            >
              <ListCollapse className="h-3.5 w-3.5" />
              {collapsed.size ? "Expand all" : "Collapse all"}
            </button>
            <span className="tabular-nums opacity-55">
              {visible.length === postings.length
                ? `${postings.length} roles`
                : `${visible.length} of ${postings.length}`}
            </span>
            <button
              className="inline-flex h-7 items-center gap-1 rounded-md bg-primary px-2 font-medium text-primary-content transition-colors hover:opacity-90"
              onClick={() => setAdding(true)}
            >
              <PlusIcon className="h-3.5 w-3.5" />
              Watch a page
            </button>
          </div>
        </div>
      </div>

      {adding && (
        <AddCompanyForm
          onClose={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            startTransition(() => router.refresh());
          }}
        />
      )}

      {/* ── Results ────────────────────────────────────────────────────── */}
      {grouped.length === 0 ? (
        <div className="rounded-box border border-dashed border-base-300 px-6 py-16 text-center">
          <Inbox className="mx-auto h-8 w-8 opacity-30" />
          <p className="mt-3 font-medium">No roles match</p>
          <p className="mt-1 text-sm opacity-60">
            {companies.length === 0
              ? "Add a company's career page to start monitoring it."
              : anyFilter
                ? "Try widening or clearing the filters."
                : "Nothing found yet — run a scan from the bar above."}
          </p>
          {companies.length === 0 && (
            <button className="btn btn-primary btn-sm mt-4 gap-1.5" onClick={() => setAdding(true)}>
              <PlusIcon className="h-4 w-4" />
              Watch a career page
            </button>
          )}
          {anyFilter && (
            <button className="btn btn-sm mt-4" onClick={clearAll}>
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          {grouped.map(([companyId, items]) => {
            const company = companies.find((c) => c.id === companyId)!;
            const companyNew = items.filter((p) => p.isNew).length;
            const isCollapsed = collapsed.has(companyId) || items.length === 0;
            const failing = company.lastStatus === "error";
            return (
              <section
                key={companyId}
                className={`overflow-hidden rounded-box border ${
                  failing ? "border-error/40" : "border-base-300"
                } ${company.active ? "" : "opacity-60"}`}
              >
                <header
                  role="button"
                  tabIndex={0}
                  aria-expanded={!isCollapsed}
                  onClick={() => items.length && toggleCompany(companyId)}
                  onKeyDown={(e) => {
                    if ((e.key === "Enter" || e.key === " ") && items.length) {
                      e.preventDefault();
                      toggleCompany(companyId);
                    }
                  }}
                  className={`flex select-none items-center gap-3 bg-base-200/50 px-3 py-2.5 transition-colors ${
                    items.length ? "cursor-pointer hover:bg-base-200" : ""
                  }`}
                >
                  {items.length === 0 ? (
                    <span className="h-4 w-4 shrink-0" />
                  ) : isCollapsed ? (
                    <ChevronRight className="h-4 w-4 shrink-0 opacity-50" />
                  ) : (
                    <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
                  )}
                  <CompanyLogo
                    name={company.name}
                    domain={company.domain}
                    logoUrl={company.logoUrl}
                    size={26}
                  />
                  <div className="min-w-0 flex-1">
                    <h2 className="truncate text-sm font-semibold">{company.name}</h2>
                    {/* A company with no roles states WHY, so "watching but
                        nothing matched" is never mistaken for "not watching". */}
                    {items.length === 0 && (
                      <p className="truncate text-xs opacity-55">
                        {failing
                          ? `scrape failed — ${company.lastError ?? "unknown error"}`
                          : !company.active
                            ? "paused"
                            : !company.seededAt
                              ? "not scanned yet"
                              : anyFilter
                                ? "no roles match the current filters"
                                : "no matching roles"}
                      </p>
                    )}
                  </div>

                  {failing && <AlertCircle className="h-4 w-4 shrink-0 text-error" />}
                  {companyNew > 0 && (
                    <span className="inline-flex h-5 items-center rounded-md bg-primary px-1.5 text-[11px] font-semibold tabular-nums text-primary-content">
                      {companyNew} new
                    </span>
                  )}
                  {items.length > 0 && (
                    <span className="text-xs tabular-nums opacity-55">{items.length}</span>
                  )}

                  {/* Per-company controls, previously stranded on a second tab. */}
                  <div
                    className="flex items-center gap-0.5"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {companyNew > 0 && (
                      <button
                        className="btn btn-ghost btn-xs btn-square"
                        onClick={() => onDismissCompany(items)}
                        title="Mark this company's new roles as seen"
                      >
                        <EyeOff className="h-3.5 w-3.5" />
                      </button>
                    )}
                    <button
                      className={`btn btn-ghost btn-xs btn-square ${configId === companyId ? "bg-base-300" : ""}`}
                      onClick={() => setConfigId(configId === companyId ? null : companyId)}
                      title="Scrape settings — roles and countries"
                    >
                      <SlidersHorizontal className="h-3.5 w-3.5" />
                    </button>
                    <button
                      className="btn btn-ghost btn-xs btn-square"
                      onClick={() =>
                        onCompanyAction(companyId, () => redetectCompany(companyId))
                      }
                      disabled={companyBusy === companyId}
                      title="Re-detect the ATS for this career page"
                    >
                      <RotateCw className="h-3.5 w-3.5" />
                    </button>
                    <input
                      type="checkbox"
                      className="toggle toggle-xs mx-1"
                      checked={company.active}
                      onChange={() =>
                        onCompanyAction(companyId, () =>
                          toggleWatchedCompany(companyId, !company.active),
                        )
                      }
                      disabled={companyBusy === companyId}
                      title={company.active ? "Pause monitoring" : "Resume monitoring"}
                    />
                    <button
                      className="btn btn-ghost btn-xs btn-square text-error"
                      onClick={() => onDeleteCompany(company, items.length)}
                      disabled={companyBusy === companyId}
                      title="Stop watching this company"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                </header>

                {configId === companyId && (
                  <div className="border-t border-base-300 px-3 py-2">
                    <ScrapeConfig company={company} onClose={() => setConfigId(null)} />
                  </div>
                )}

                <ul
                  className={`divide-y divide-base-300 border-t border-base-300 ${isCollapsed ? "hidden" : ""}`}
                >
                  {items.map((posting) => (
                    <li
                      key={posting.id}
                      className="flex items-center gap-3 px-4 py-2 hover:bg-base-200/40"
                    >
                      <div className="min-w-0 flex-1">
                        <a
                          href={posting.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-sm font-medium hover:underline"
                        >
                          {posting.title}
                        </a>
                        {posting.isNew && (
                          <span className="ml-2 inline-flex h-[18px] items-center rounded-md bg-primary px-1.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-primary-content">
                            new
                          </span>
                        )}
                      </div>

                      <span
                        title={posting.matchedRole}
                        className="hidden h-[22px] w-36 shrink-0 items-center justify-center truncate whitespace-nowrap rounded-md bg-base-300/70 px-2 text-[11px] font-medium sm:inline-flex"
                      >
                        {ROLE_SHORT[posting.matchedRole] ?? posting.matchedRole}
                      </span>
                      <span className="hidden w-44 shrink-0 items-center gap-1 truncate text-xs opacity-60 lg:flex">
                        {posting.location && (
                          <>
                            <MapPin className="h-3 w-3 shrink-0" />
                            {posting.location}
                          </>
                        )}
                      </span>
                      <span
                        className="hidden w-16 shrink-0 text-right text-xs tabular-nums opacity-60 md:block"
                        title={
                          posting.minYears === null
                            ? "Experience not stated in the job description"
                            : `${posting.minYears}+ years of experience`
                        }
                      >
                        {posting.minYears === null ? "—" : `${posting.minYears}+ yrs`}
                      </span>
                      <span className="hidden w-28 shrink-0 text-right text-xs opacity-60 md:block">
                        {postedLabel(posting)}
                      </span>

                      {posting.jobApplicationId ? (
                        <span
                          className="inline-flex h-7 w-[84px] shrink-0 items-center justify-center gap-1 rounded-md bg-success/10 text-xs font-medium text-success"
                          title="Already in your applications"
                        >
                          <Check className="h-3.5 w-3.5" />
                          Applied
                        </span>
                      ) : (
                        <button
                          className="inline-flex h-7 w-[84px] shrink-0 items-center justify-center gap-1 rounded-md border border-base-300 bg-base-100 text-xs font-medium transition-colors hover:border-primary hover:bg-primary hover:text-primary-content disabled:opacity-50"
                          onClick={() => onTrack(posting)}
                          disabled={busyId === posting.id}
                          title="Add to your applications as Applied"
                        >
                          <Plus className="h-3.5 w-3.5" />
                          Applied
                        </button>
                      )}
                      <a
                        href={posting.url}
                        target="_blank"
                        rel="noreferrer"
                        className="btn btn-ghost btn-xs btn-square shrink-0"
                        title="Open posting"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </li>
                  ))}
                </ul>
              </section>
            );
          })}
        </div>
      )}
    </>
  );
}
