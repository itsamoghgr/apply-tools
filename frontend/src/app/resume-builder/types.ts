// Structured resume shape shared by the builder UI, server actions, and the
// backend renderer (backend/resume_render.py). Keep these in sync.

export type ResumeHeader = {
  fullName: string;
  phone: string;
  email: string;
  linkedin: string;
  github: string;
  portfolio: string;
  scholar: string;
  location: string;
};

// Structured date range shared by education + experience entries. Month is 1–12
// (0/null = unset); year is a 4-digit number (null = unset). `isPresent` marks an
// ongoing entry whose end is "now" — the end month/year are ignored when it's set.
// The literal display string ("May 2025 – Dec 2025") is DERIVED via formatDateRange,
// so it stays consistent and is reliably computable.
export type DateParts = {
  startMonth: number | null;
  startYear: number | null;
  endMonth: number | null;
  endYear: number | null;
  isPresent: boolean;
};

export type EducationEntry = DateParts & {
  school: string;
  degree: string;
  location: string;
};

export type ExperienceEntry = DateParts & {
  company: string;
  title: string;
  location: string;
  bullets: string[];
};

export type SkillEntry = {
  category: string;
  items: string;
};

export type ProjectEntry = {
  name: string;
  date: string;
  bullets: string[];
};

// ── Structured date helpers ─────────────────────────────────────────────────
// Short month names, index 0 unused so MONTHS[1]="Jan" (months are 1–12).
export const MONTHS = [
  "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
] as const;

// Map any month spelling in stored/AI text to its 1–12 number: "Jan", "Sept",
// "July", "September" all resolve. Returns null if unrecognized.
const MONTH_LOOKUP: Record<string, number> = (() => {
  const full = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
  ];
  const m: Record<string, number> = {};
  full.forEach((name, i) => {
    m[name] = i + 1;
    m[name.slice(0, 3)] = i + 1; // jan, feb, …
  });
  m["sept"] = 9; // common 4-letter abbreviation
  return m;
})();

function monthToNum(token: string): number | null {
  return MONTH_LOOKUP[token.trim().toLowerCase()] ?? null;
}

const emptyDateParts = (): DateParts => ({
  startMonth: null,
  startYear: null,
  endMonth: null,
  endYear: null,
  isPresent: false,
});

// One endpoint of a range: "May 2025" / "2025" / "Present". Returns the parsed
// month/year and whether it read as "present/current/now".
function parseEndpoint(raw: string): {
  month: number | null;
  year: number | null;
  present: boolean;
} {
  const s = raw.trim();
  if (/^(present|current|now|ongoing)$/i.test(s)) {
    return { month: null, year: null, present: true };
  }
  const yearMatch = s.match(/\b(19|20)\d{2}\b/);
  const year = yearMatch ? Number(yearMatch[0]) : null;
  const monthMatch = s.match(/[A-Za-z]+/);
  const month = monthMatch ? monthToNum(monthMatch[0]) : null;
  return { month, year, present: false };
}

// Parse a free-text date string ("May 2025 – Dec 2025", "2024 – Present",
// "May 2026") into structured DateParts. Handles en-dash/hyphen/"to" separators
// and lone dates (mapped to the END, e.g. a graduation date). Best-effort: fields
// that don't parse stay null. Used for the one-time backfill and to coerce any
// legacy/AI `dates` string on load.
export function parseDateString(raw: unknown): DateParts {
  const out = emptyDateParts();
  if (typeof raw !== "string" || !raw.trim()) return out;
  // Split on en/em dash, hyphen (with optional spaces), or the word "to".
  const parts = raw.split(/\s*(?:–|—|-|\bto\b)\s*/i).filter((p) => p.trim());
  if (parts.length >= 2) {
    const a = parseEndpoint(parts[0]);
    const b = parseEndpoint(parts[parts.length - 1]);
    out.startMonth = a.month;
    out.startYear = a.year;
    if (b.present) {
      out.isPresent = true;
    } else {
      out.endMonth = b.month;
      out.endYear = b.year;
    }
  } else if (parts.length === 1) {
    // Lone date → treat as the end (e.g. a graduation month).
    const a = parseEndpoint(parts[0]);
    if (a.present) out.isPresent = true;
    else {
      out.endMonth = a.month;
      out.endYear = a.year;
    }
  }
  return out;
}

// Render one endpoint for display: "May 2025", "2025", or "" if nothing set.
function formatEndpoint(month: number | null, year: number | null): string {
  const mm = month && month >= 1 && month <= 12 ? MONTHS[month] : "";
  const yy = year ? String(year) : "";
  return [mm, yy].filter(Boolean).join(" ");
}

// The literal string shown on the resume, derived from the structured fields:
// "May 2025 – Dec 2025", "Aug 2024 – Present", or a lone endpoint. Empty when
// nothing is set.
export function formatDateRange(d: DateParts): string {
  const start = formatEndpoint(d.startMonth, d.startYear);
  const end = d.isPresent ? "Present" : formatEndpoint(d.endMonth, d.endYear);
  if (start && end) return `${start} – ${end}`;
  return start || end || "";
}

// Total months of experience across entries, MERGING overlapping periods so the
// result is true calendar time (two overlapping 1-year jobs ≈ 1 year, not 2).
// "Present" resolves to `now` (defaults to the current month). Entries missing a
// usable start are skipped. Returns whole months.
export function experienceMonths(
  entries: DateParts[],
  now: Date = new Date(),
): number {
  const nowIdx = now.getFullYear() * 12 + now.getMonth(); // 0-based month index
  // Convert each entry to an inclusive [startIdx, endIdx] in absolute months.
  const ranges: [number, number][] = [];
  for (const e of entries) {
    if (!e.startYear || !e.startMonth) continue;
    const startIdx = e.startYear * 12 + (e.startMonth - 1);
    let endIdx: number;
    if (e.isPresent) endIdx = nowIdx;
    else if (e.endYear && e.endMonth) endIdx = e.endYear * 12 + (e.endMonth - 1);
    else continue; // no usable end
    if (endIdx < startIdx) continue; // malformed
    ranges.push([startIdx, endIdx]);
  }
  if (!ranges.length) return 0;
  // Merge overlaps, then sum spans (inclusive of both endpoint months).
  ranges.sort((a, b) => a[0] - b[0]);
  let total = 0;
  let [curS, curE] = ranges[0];
  for (let i = 1; i < ranges.length; i++) {
    const [s, e] = ranges[i];
    if (s <= curE + 1) {
      curE = Math.max(curE, e); // overlapping or adjacent → merge
    } else {
      total += curE - curS + 1;
      [curS, curE] = [s, e];
    }
  }
  total += curE - curS + 1;
  return total;
}

// Human label for a month count: "3 yrs 2 mos", "1 yr", "5 mos", or "" for 0.
export function formatDuration(totalMonths: number): string {
  if (totalMonths <= 0) return "";
  const years = Math.floor(totalMonths / 12);
  const months = totalMonths % 12;
  const parts: string[] = [];
  if (years) parts.push(`${years} ${years === 1 ? "yr" : "yrs"}`);
  if (months) parts.push(`${months} ${months === 1 ? "mo" : "mos"}`);
  return parts.join(" ");
}

// The reorderable/toggleable sections (header is fixed at the very top and is
// not part of this list). Order here is the DEFAULT order when a profile has no
// explicit sectionOrder yet — matching how resumes rendered before this feature.
export const SECTION_KEYS = [
  "summary",
  "education",
  "experience",
  "skills",
  "projects",
] as const;
export type SectionKey = (typeof SECTION_KEYS)[number];

// Human labels for the section-manager UI (the LaTeX/plaintext renderers use
// their own section headings, e.g. "PROFESSIONAL EXPERIENCE").
export const SECTION_LABELS: Record<SectionKey, string> = {
  summary: "Summary",
  education: "Education",
  experience: "Experience",
  skills: "Skills",
  projects: "Projects",
};

// One entry in a profile's ordered section list: which section, shown or hidden.
export type SectionMeta = { key: SectionKey; visible: boolean };

export type ResumeProfileData = {
  header: ResumeHeader;
  summary: string;
  education: EducationEntry[];
  experience: ExperienceEntry[];
  skills: SkillEntry[];
  projects: ProjectEntry[];
  // Ordered, visibility-aware section list. Always fully populated after
  // normalizeProfile (missing/legacy keys are appended in default order).
  sectionOrder: SectionMeta[];
};

// Default section list: the canonical order, all visible. Used when a profile
// has no stored order (every pre-existing resume), so nothing changes visually
// until the user reorders/hides something.
export const defaultSectionOrder = (): SectionMeta[] =>
  SECTION_KEYS.map((key) => ({ key, visible: true }));

export const emptyHeader = (): ResumeHeader => ({
  fullName: "",
  phone: "",
  email: "",
  linkedin: "",
  github: "",
  portfolio: "",
  scholar: "",
  location: "",
});

export const emptyEducation = (): EducationEntry => ({
  ...emptyDateParts(),
  school: "",
  degree: "",
  location: "",
});

export const emptyExperience = (): ExperienceEntry => ({
  ...emptyDateParts(),
  company: "",
  title: "",
  location: "",
  bullets: [""],
});

export const emptySkill = (): SkillEntry => ({ category: "", items: "" });

export const emptyProject = (): ProjectEntry => ({
  name: "",
  date: "",
  bullets: [""],
});

export const emptyProfile = (): ResumeProfileData => ({
  header: emptyHeader(),
  summary: "",
  education: [],
  experience: [],
  skills: [],
  projects: [],
  sectionOrder: defaultSectionOrder(),
});

// Reconcile a possibly-partial/legacy sectionOrder against the known section
// keys: keep valid stored entries in their order, drop unknown keys, and append
// any missing sections (all visible) so every section is always accounted for.
// An empty/absent list therefore yields the full default order — pre-existing
// resumes are unaffected.
export function normalizeSectionOrder(raw: unknown): SectionMeta[] {
  const known = new Set<string>(SECTION_KEYS);
  const seen = new Set<SectionKey>();
  const out: SectionMeta[] = [];
  if (Array.isArray(raw)) {
    for (const item of raw) {
      const o = (item ?? {}) as Record<string, unknown>;
      const key = o.key;
      if (typeof key === "string" && known.has(key) && !seen.has(key as SectionKey)) {
        out.push({ key: key as SectionKey, visible: o.visible !== false });
        seen.add(key as SectionKey);
      }
    }
  }
  for (const key of SECTION_KEYS) {
    if (!seen.has(key)) out.push({ key, visible: true });
  }
  return out;
}

// Bullets are authored/stored as markdown (**bold**, *italic*). Legacy data
// (and older AI output) may contain raw \textbf{...}/\emph{...}; convert those
// to markdown on load so they display with emphasis in the editor.
export function legacyToMarkdown(text: string): string {
  return text
    .replace(/\\textbf\{([^{}]*)\}/g, (_m, inner) => `**${unescapeLatex(inner)}**`)
    .replace(/\\emph\{([^{}]*)\}/g, (_m, inner) => `*${unescapeLatex(inner)}*`);
}

function unescapeLatex(s: string): string {
  return s.replace(/\\([&%$#_{}])/g, "$1");
}

// Coerce arbitrary JSON (from Prisma / AI responses) into a well-formed profile
// so the editor never crashes on a missing field.
export function normalizeProfile(raw: unknown): ResumeProfileData {
  const p = (raw ?? {}) as Record<string, unknown>;
  const h = (p.header ?? {}) as Record<string, unknown>;
  const str = (v: unknown): string => (typeof v === "string" ? v : "");
  const arr = <T>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);
  // Bullet arrays additionally get legacy \textbf/\emph → markdown conversion.
  const bulletArr = (v: unknown): string[] =>
    Array.isArray(v)
      ? v.map((x) => (typeof x === "string" ? legacyToMarkdown(x) : ""))
      : [];
  const num = (v: unknown): number | null =>
    typeof v === "number" && Number.isFinite(v) ? v : null;
  // Coerce an entry's date info. Prefer explicit structured fields; if none are
  // present (legacy row or AI draft that emitted a `dates` string), parse the
  // free-text `dates` into structured fields so everything downstream is uniform.
  const dateParts = (e: Record<string, unknown>): DateParts => {
    const hasStructured =
      "startMonth" in e || "startYear" in e || "endYear" in e || "isPresent" in e;
    if (hasStructured) {
      return {
        startMonth: num(e.startMonth),
        startYear: num(e.startYear),
        endMonth: num(e.endMonth),
        endYear: num(e.endYear),
        isPresent: e.isPresent === true,
      };
    }
    return parseDateString(e.dates);
  };

  return {
    header: {
      fullName: str(h.fullName),
      phone: str(h.phone),
      email: str(h.email),
      linkedin: str(h.linkedin),
      github: str(h.github),
      portfolio: str(h.portfolio),
      scholar: str(h.scholar),
      location: str(h.location),
    },
    summary: str(p.summary),
    sectionOrder: normalizeSectionOrder(p.sectionOrder),
    education: arr<Record<string, unknown>>(p.education).map((e) => ({
      ...dateParts(e),
      school: str(e.school),
      degree: str(e.degree),
      location: str(e.location),
    })),
    experience: arr<Record<string, unknown>>(p.experience).map((x) => ({
      ...dateParts(x),
      company: str(x.company),
      title: str(x.title),
      location: str(x.location),
      bullets: bulletArr(x.bullets),
    })),
    skills: arr<Record<string, unknown>>(p.skills).map((s) => ({
      category: str(s.category),
      items: str(s.items),
    })),
    projects: arr<Record<string, unknown>>(p.projects).map((pr) => ({
      name: str(pr.name),
      date: str(pr.date),
      bullets: bulletArr(pr.bullets),
    })),
  };
}
