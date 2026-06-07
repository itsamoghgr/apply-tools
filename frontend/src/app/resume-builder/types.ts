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

export type EducationEntry = {
  school: string;
  dates: string;
  degree: string;
  location: string;
};

export type ExperienceEntry = {
  company: string;
  dates: string;
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

export type ResumeProfileData = {
  header: ResumeHeader;
  education: EducationEntry[];
  experience: ExperienceEntry[];
  skills: SkillEntry[];
  projects: ProjectEntry[];
};

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
  school: "",
  dates: "",
  degree: "",
  location: "",
});

export const emptyExperience = (): ExperienceEntry => ({
  company: "",
  dates: "",
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
  education: [],
  experience: [],
  skills: [],
  projects: [],
});

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
    education: arr<Record<string, unknown>>(p.education).map((e) => ({
      school: str(e.school),
      dates: str(e.dates),
      degree: str(e.degree),
      location: str(e.location),
    })),
    experience: arr<Record<string, unknown>>(p.experience).map((x) => ({
      company: str(x.company),
      dates: str(x.dates),
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
