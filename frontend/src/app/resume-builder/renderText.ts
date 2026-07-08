// Render a structured builder profile to clean plain text — the same shape the
// backend's profile_to_text (backend/resume_ai.py) produces. This becomes the
// `content` of the companion Resume row, which the applications / reach-out / AI
// pickers feed to the LLM as the resume "voice / ground truth." Keep the two
// renderers in sync so a builder resume reads the same whether scored in-editor
// (backend) or consumed by a downstream generator (this text).

import type { ResumeProfileData } from "./types";

// Strip markdown bold/italic markers so the model reads prose, not syntax.
// Mirrors _plain() on the backend.
function plain(v: unknown): string {
  if (typeof v !== "string") return "";
  return v
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .trim();
}

export function profileToText(profile: ResumeProfileData): string {
  const lines: string[] = [];

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

  const education = (profile.education ?? []).filter((e) => plain(e.school));
  if (education.length) {
    lines.push("\nEDUCATION");
    for (const e of education) {
      const head = [plain(e.degree), plain(e.school)].filter(Boolean).join(", ");
      const tail = [plain(e.location), plain(e.dates)].filter(Boolean).join(" | ");
      lines.push(`- ${head}${tail ? ` (${tail})` : ""}`);
    }
  }

  const experience = (profile.experience ?? []).filter((x) => plain(x.company));
  if (experience.length) {
    lines.push("\nPROFESSIONAL EXPERIENCE");
    for (const x of experience) {
      const head = [plain(x.title), plain(x.company)].filter(Boolean).join(" — ");
      const tail = [plain(x.location), plain(x.dates)].filter(Boolean).join(" | ");
      lines.push(`\n${head}${tail ? ` (${tail})` : ""}`);
      for (const b of x.bullets ?? []) {
        const bt = plain(b);
        if (bt) lines.push(`  - ${bt}`);
      }
    }
  }

  const skills = (profile.skills ?? []).filter((s) => plain(s.items));
  if (skills.length) {
    lines.push("\nTECHNICAL SKILLS");
    for (const s of skills) {
      const cat = plain(s.category);
      lines.push(`- ${cat ? `${cat}: ` : ""}${plain(s.items)}`);
    }
  }

  const projects = (profile.projects ?? []).filter((pr) => plain(pr.name));
  if (projects.length) {
    lines.push("\nPROJECTS");
    for (const pr of projects) {
      const dt = plain(pr.date);
      lines.push(`\n${plain(pr.name)}${dt ? ` (${dt})` : ""}`);
      for (const b of pr.bullets ?? []) {
        const bt = plain(b);
        if (bt) lines.push(`  - ${bt}`);
      }
    }
  }

  return lines.join("\n").trim();
}
