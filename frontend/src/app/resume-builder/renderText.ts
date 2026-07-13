// Render a structured builder profile to clean plain text — the same shape the
// backend's profile_to_text (backend/resume_ai.py) produces. This becomes the
// `content` of the companion Resume row, which the applications / reach-out / AI
// pickers feed to the LLM as the resume "voice / ground truth." Keep the two
// renderers in sync so a builder resume reads the same whether scored in-editor
// (backend) or consumed by a downstream generator (this text).

import {
  defaultSectionOrder,
  formatDateRange,
  type ResumeProfileData,
  type SectionKey,
} from "./types";

// Strip markdown bold/italic markers so the model reads prose, not syntax.
// Mirrors _plain() on the backend.
function plain(v: unknown): string {
  if (typeof v !== "string") return "";
  return v
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .trim();
}

// Per-section text emitters. Each returns "" when the section is empty, so a
// hidden-or-empty section contributes nothing. Kept in lockstep with the Python
// profile_to_text (backend/resume_ai.py).
function summaryText(profile: ResumeProfileData): string {
  const s = plain(profile.summary);
  return s ? `\nSUMMARY\n${s}` : "";
}

function educationText(profile: ResumeProfileData): string {
  const rows = (profile.education ?? []).filter((e) => plain(e.school));
  if (!rows.length) return "";
  const lines = ["\nEDUCATION"];
  for (const e of rows) {
    const head = [plain(e.degree), plain(e.school)].filter(Boolean).join(", ");
    const tail = [plain(e.location), formatDateRange(e)].filter(Boolean).join(" | ");
    lines.push(`- ${head}${tail ? ` (${tail})` : ""}`);
  }
  return lines.join("\n");
}

function experienceText(profile: ResumeProfileData): string {
  const rows = (profile.experience ?? []).filter((x) => plain(x.company));
  if (!rows.length) return "";
  const lines = ["\nPROFESSIONAL EXPERIENCE"];
  for (const x of rows) {
    const head = [plain(x.title), plain(x.company)].filter(Boolean).join(" — ");
    const tail = [plain(x.location), formatDateRange(x)].filter(Boolean).join(" | ");
    lines.push(`\n${head}${tail ? ` (${tail})` : ""}`);
    for (const b of x.bullets ?? []) {
      const bt = plain(b);
      if (bt) lines.push(`  - ${bt}`);
    }
  }
  return lines.join("\n");
}

function skillsText(profile: ResumeProfileData): string {
  const rows = (profile.skills ?? []).filter((s) => plain(s.items));
  if (!rows.length) return "";
  const lines = ["\nTECHNICAL SKILLS"];
  for (const s of rows) {
    const cat = plain(s.category);
    lines.push(`- ${cat ? `${cat}: ` : ""}${plain(s.items)}`);
  }
  return lines.join("\n");
}

function projectsText(profile: ResumeProfileData): string {
  const rows = (profile.projects ?? []).filter((pr) => plain(pr.name));
  if (!rows.length) return "";
  const lines = ["\nPROJECTS"];
  for (const pr of rows) {
    const dt = plain(pr.date);
    lines.push(`\n${plain(pr.name)}${dt ? ` (${dt})` : ""}`);
    for (const b of pr.bullets ?? []) {
      const bt = plain(b);
      if (bt) lines.push(`  - ${bt}`);
    }
  }
  return lines.join("\n");
}

const SECTION_TEXT: Record<
  SectionKey,
  (profile: ResumeProfileData) => string
> = {
  summary: summaryText,
  education: educationText,
  experience: experienceText,
  skills: skillsText,
  projects: projectsText,
};

export function profileToText(profile: ResumeProfileData): string {
  const lines: string[] = [];

  // Header is always first, above the reorderable sections.
  const header = profile.header ?? ({} as ResumeProfileData["header"]);
  lines.push(plain(header.fullName) || "Resume");
  const contact = [
    plain(header.location),
    plain(header.email),
    plain(header.phone),
    plain(header.linkedin),
    plain(header.github),
    plain(header.portfolio),
  ]
    .filter(Boolean)
    .join(" | ");
  if (contact) lines.push(contact);

  // Emit sections in the profile's order, skipping hidden ones. Fall back to the
  // default order if none is set (older profiles).
  const order =
    profile.sectionOrder && profile.sectionOrder.length
      ? profile.sectionOrder
      : defaultSectionOrder();
  for (const { key, visible } of order) {
    if (!visible) continue;
    const block = SECTION_TEXT[key]?.(profile);
    if (block) lines.push(block);
  }

  return lines.join("\n").trim();
}
