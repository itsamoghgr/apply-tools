// Types for the master Profile feature. These are the shapes used by server
// actions and consumed by the UI. They mirror the Prisma models but omit
// internal fields (profileId, createdAt) that the UI never touches directly.

export type ProfileHeader = {
  fullName: string;
  email: string;
  phone: string;
  location: string;
  linkedin: string;
  github: string;
  portfolio: string;
};

export type ExperiencePayload = {
  company: string;
  title: string;
  location: string;
  startDate: string;
  endDate: string;
  bullets: string[];
};

export type ProjectPayload = {
  name: string;
  date: string;
  link: string;
  bullets: string[];
};

export type SkillPayload = {
  category: string;
  items: string[];
};

// Full profile returned by getProfile(), child rows are ordered by `order`.
export type ProfileData = ProfileHeader & {
  id: string;
  experiences: (ExperiencePayload & { id: string; order: number })[];
  projects: (ProjectPayload & { id: string; order: number })[];
  skills: (SkillPayload & { id: string; order: number })[];
};

// ── Normalizers ───────────────────────────────────────────────────────────────
// Coerce untrusted input (AI drafts, form data) into well-formed payloads.

const str = (v: unknown): string => (typeof v === "string" ? v.trim() : "");
const strArr = (v: unknown): string[] =>
  Array.isArray(v) ? v.map((x) => (typeof x === "string" ? x.trim() : "")) : [];

export function normalizeHeader(raw: unknown): ProfileHeader {
  const h = (raw ?? {}) as Record<string, unknown>;
  return {
    fullName: str(h.fullName),
    email: str(h.email),
    phone: str(h.phone),
    location: str(h.location),
    linkedin: str(h.linkedin),
    github: str(h.github),
    portfolio: str(h.portfolio),
  };
}

export function normalizeExperience(raw: unknown): ExperiencePayload {
  const e = (raw ?? {}) as Record<string, unknown>;
  return {
    company: str(e.company),
    title: str(e.title),
    location: str(e.location),
    startDate: str(e.startDate),
    endDate: str(e.endDate),
    bullets: strArr(e.bullets),
  };
}

export function normalizeProject(raw: unknown): ProjectPayload {
  const p = (raw ?? {}) as Record<string, unknown>;
  return {
    name: str(p.name),
    date: str(p.date),
    link: str(p.link),
    bullets: strArr(p.bullets),
  };
}

export function normalizeSkill(raw: unknown): SkillPayload {
  const s = (raw ?? {}) as Record<string, unknown>;
  return {
    category: str(s.category),
    items: strArr(s.items),
  };
}
