"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { Prisma } from "@prisma/client";
import { prisma } from "@/lib/prisma";

// Watchlist CRUD runs through Prisma directly, the same way /applications and
// /resumes do. The agent service (:8002) is only involved for things it alone
// can do: detecting an ATS, scraping, and running the schedules.
const AGENT_URL = process.env.AGENT_URL ?? "http://127.0.0.1:8002";

export type FormState = { error?: string; ok?: boolean; message?: string };

/**
 * Update a company's SCRAPE CONFIG — the filters applied while scraping, which
 * permanently discard non-matching postings rather than hiding them.
 *
 * Empty role list / country list means "no restriction", never "match nothing":
 * an empty filter that silently matched zero postings would look identical to a
 * broken scraper.
 */
export async function updateScrapeConfig(
  id: string,
  config: { roleFilter: string[]; countryFilter: string[] },
): Promise<FormState> {
  await prisma.watchedCompany.update({
    where: { id },
    data: {
      roleFilter: config.roleFilter.length ? config.roleFilter : Prisma.DbNull,
      countryFilter: config.countryFilter.length ? config.countryFilter : Prisma.DbNull,
    },
  });
  revalidatePath("/job-board");
  return { ok: true, message: "Scrape settings saved." };
}

const CompanyInput = z.object({
  name: z.string().min(1, "Company name is required").max(300),
  careerUrl: z.string().min(1, "Career page URL is required").max(2000),
  domain: z.string().max(255).optional().nullable(),
  logoUrl: z.string().max(2000).optional().nullable(),
  roleFilter: z.array(z.string()).optional().nullable(),
});

/** Best-effort host extraction; used to derive a logo when none is given. */
function hostFrom(raw: string): string | null {
  try {
    const url = raw.includes("://") ? raw : `https://${raw}`;
    return new URL(url).hostname.replace(/^www\./, "").toLowerCase() || null;
  } catch {
    return null;
  }
}

/** Ask the agent service what ATS a career URL belongs to. */
async function detectAts(
  careerUrl: string,
): Promise<{ ats: string | null; slug: string | null }> {
  try {
    const res = await fetch(`${AGENT_URL}/api/v1/jobboard/detect`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ career_url: careerUrl }),
      cache: "no-store",
    });
    if (!res.ok) return { ats: null, slug: null };
    const data = await res.json();
    return { ats: data.ats ?? null, slug: data.slug ?? null };
  } catch {
    // The agent server being down must not block adding a company — the
    // monitor re-detects on its first cycle and persists the result then.
    return { ats: null, slug: null };
  }
}

export async function addWatchedCompany(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const rawRoles = formData.getAll("roleFilter").map(String).filter(Boolean);
  const parsed = CompanyInput.safeParse({
    name: formData.get("name"),
    careerUrl: formData.get("careerUrl"),
    domain: formData.get("domain") || null,
    logoUrl: formData.get("logoUrl") || null,
    roleFilter: rawRoles.length ? rawRoles : null,
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
  }

  const { name, careerUrl, logoUrl, roleFilter } = parsed.data;
  const domain = parsed.data.domain?.trim() || hostFrom(careerUrl);
  const { ats, slug } = await detectAts(careerUrl);

  const existing = await prisma.watchedCompany.findUnique({ where: { careerUrl } });
  if (existing) {
    return { error: `"${existing.name}" is already watching that career page.` };
  }

  await prisma.watchedCompany.create({
    data: {
      id: crypto.randomUUID().replace(/-/g, "").slice(0, 16),
      name: name.trim(),
      careerUrl: careerUrl.trim(),
      domain,
      logoUrl: logoUrl?.trim() || null,
      ats,
      atsSlug: slug,
      roleFilter: roleFilter ?? undefined,
      active: true,
    },
  });

  revalidatePath("/job-board");
  return { ok: true, message: `Now watching ${name}.` };
}

export async function toggleWatchedCompany(id: string, active: boolean) {
  await prisma.watchedCompany.update({ where: { id }, data: { active } });
  revalidatePath("/job-board");
}

/**
 * Remove a company from the watchlist.
 *
 * Its postings cascade-delete with it (they are only meaningful in the context
 * of the board they came from). Anything already pushed into the applications
 * tracker survives — JobPosting.jobApplicationId is the pointer, and the
 * JobApplication row is independent.
 */
export async function deleteWatchedCompany(id: string) {
  await prisma.watchedCompany.delete({ where: { id } });
  revalidatePath("/job-board");
}

/** Re-run ATS detection, e.g. after a company migrates job boards. */
export async function redetectCompany(id: string): Promise<FormState> {
  const company = await prisma.watchedCompany.findUnique({ where: { id } });
  if (!company) return { error: "Company not found." };
  const { ats, slug } = await detectAts(company.careerUrl);
  if (!ats) return { error: "Could not reach the agent service to re-detect." };
  await prisma.watchedCompany.update({
    where: { id },
    data: { ats, atsSlug: slug },
  });
  revalidatePath("/job-board");
  return { ok: true, message: `Detected ${ats}.` };
}

/**
 * Push a posting into the applications tracker.
 *
 * Creates a JobApplication pre-filled from the posting and stamps the link back
 * onto the posting, so the Job Board can show what has already been actioned.
 * Idempotent: a posting already linked returns its existing application.
 */
export async function trackPosting(postingId: string): Promise<FormState> {
  const posting = await prisma.jobPosting.findUnique({
    where: { id: postingId },
    include: { watchedCompany: true },
  });
  if (!posting) return { error: "Posting not found." };
  if (posting.jobApplicationId) {
    return { ok: true, message: "Already in your applications." };
  }

  // appliedDate MUST be UTC midnight of the calendar day. The dashboard groups
  // by day off this column, and a timestamp with a time component lands in the
  // wrong bucket (this is what the SQLite→Postgres migration got wrong).
  const now = new Date();
  const appliedDate = new Date(
    Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()),
  );

  const application = await prisma.jobApplication.create({
    data: {
      id: crypto.randomUUID().replace(/-/g, "").slice(0, 16),
      companyName: posting.watchedCompany.name,
      jobRole: posting.title,
      jobUrl: posting.url,
      location: posting.location,
      companyCareerPage: posting.watchedCompany.careerUrl,
      // Must be one of backend/server.py ALLOWED_STATUSES. A tracked posting
      // hasn't been applied to yet, but "Applied" is the only sane entry point
      // in the existing vocabulary — the user moves it on from there.
      status: "Applied",
      appliedDate,
    },
  });

  await prisma.jobPosting.update({
    where: { id: postingId },
    data: { jobApplicationId: application.id },
  });

  revalidatePath("/job-board");
  revalidatePath("/applications");
  return { ok: true, message: `Added ${posting.title} to applications.` };
}

/** Mark postings as seen so they leave the "new" feed without being deleted. */
export async function dismissPostings(ids: string[]) {
  if (!ids.length) return;
  await prisma.jobPosting.updateMany({
    where: { id: { in: ids } },
    data: { isNew: false },
  });
  revalidatePath("/job-board");
}


/** Scrape-level retention: how many weeks of postings stay in the live feed. */
export async function setRetentionWeeks(weeks: number): Promise<FormState> {
  if (weeks < 1 || weeks > 52) return { error: "Pick between 1 and 52 weeks." };
  await prisma.setting.upsert({
    where: { key: "jobboard.retentionWeeks" },
    create: { key: "jobboard.retentionWeeks", value: String(weeks) },
    update: { value: String(weeks) },
  });
  revalidatePath("/job-board");
  return { ok: true, message: `Keeping the last ${weeks} weeks.` };
}


/**
 * Scrape-wide maximum years of experience.
 *
 * 0 means "no limit". Applying it ARCHIVES postings above the ceiling and
 * RESTORES any that fit again, so the setting is reversible in both directions
 * and nothing is ever deleted. Postings with no stated requirement are kept.
 */
export async function setMaxYears(maxYears: number): Promise<FormState> {
  if (maxYears < 0 || maxYears > 50) return { error: "Pick between 0 and 50 years." };
  await prisma.setting.upsert({
    where: { key: "jobboard.maxYears" },
    create: { key: "jobboard.maxYears", value: String(maxYears) },
    update: { value: String(maxYears) },
  });

  // Bring stored postings in line immediately rather than waiting for the next
  // 3-hourly scan — the change should be visible the moment it is made.
  const retention = await prisma.setting.findUnique({
    where: { key: "jobboard.retentionWeeks" },
  });
  const weeks = Number(retention?.value ?? 4) || 4;
  const cutoff = new Date(Date.now() - weeks * 7 * 86_400_000);

  let archived = 0;
  let restored = 0;
  if (maxYears > 0) {
    archived = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: null,
          jobApplicationId: null,
          minYears: { gt: maxYears },
        },
        data: { archivedAt: new Date() },
      })
    ).count;
    restored = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: { not: null },
          minYears: { lte: maxYears, not: null },
          OR: [{ postedAt: { gt: cutoff } }, { postedAt: null, firstSeenAt: { gt: cutoff } }],
        },
        data: { archivedAt: null },
      })
    ).count;
  } else {
    // No limit: restore everything the ceiling had hidden, within retention.
    restored = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: { not: null },
          minYears: { not: null },
          OR: [{ postedAt: { gt: cutoff } }, { postedAt: null, firstSeenAt: { gt: cutoff } }],
        },
        data: { archivedAt: null },
      })
    ).count;
  }

  revalidatePath("/job-board");
  const parts = [
    archived ? `${archived} hidden` : "",
    restored ? `${restored} restored` : "",
  ].filter(Boolean);
  return {
    ok: true,
    message: maxYears
      ? `Keeping roles up to ${maxYears} years${parts.length ? ` — ${parts.join(", ")}` : ""}.`
      : `No experience limit${parts.length ? ` — ${parts.join(", ")}` : ""}.`,
  };
}


/**
 * Scrape-wide country allow-list (ISO alpha-2). Empty = anywhere.
 *
 * A DEFAULT, not an override: a company with its own country list keeps it.
 * Applying it archives stored postings outside the list and restores any that
 * fit again — nothing is deleted, and postings whose country couldn't be parsed
 * are always kept.
 */
export async function setGlobalCountries(codes: string[]): Promise<FormState> {
  const value = codes.map((c) => c.toUpperCase()).join(",");
  await prisma.setting.upsert({
    where: { key: "jobboard.countries" },
    create: { key: "jobboard.countries", value },
    update: { value },
  });

  const retention = await prisma.setting.findUnique({
    where: { key: "jobboard.retentionWeeks" },
  });
  const weeks = Number(retention?.value ?? 4) || 4;
  const cutoff = new Date(Date.now() - weeks * 7 * 86_400_000);

  let archived = 0;
  let restored = 0;
  if (codes.length) {
    archived = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: null,
          jobApplicationId: null,
          // `country: null` is NOT matched — an unparseable location is never
          // grounds for hiding a posting.
          country: { notIn: codes, not: null },
        },
        data: { archivedAt: new Date() },
      })
    ).count;
    restored = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: { not: null },
          country: { in: codes },
          OR: [{ postedAt: { gt: cutoff } }, { postedAt: null, firstSeenAt: { gt: cutoff } }],
        },
        data: { archivedAt: null },
      })
    ).count;
  } else {
    restored = (
      await prisma.jobPosting.updateMany({
        where: {
          archivedAt: { not: null },
          country: { not: null },
          OR: [{ postedAt: { gt: cutoff } }, { postedAt: null, firstSeenAt: { gt: cutoff } }],
        },
        data: { archivedAt: null },
      })
    ).count;
  }

  revalidatePath("/job-board");
  const parts = [
    archived ? `${archived} hidden` : "",
    restored ? `${restored} restored` : "",
  ].filter(Boolean);
  const label = codes.length ? codes.join(", ") : "anywhere";
  return {
    ok: true,
    message: `Countries: ${label}${parts.length ? ` — ${parts.join(", ")}` : ""}.`,
  };
}
