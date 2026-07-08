"use server";

import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { emptyProfile, normalizeProfile, type ResumeProfileData } from "./types";
import { profileToText } from "./renderText";

// Upsert the companion plaintext Resume for a builder profile so it shows up in
// the applications / reach-out / AI pickers (they select active Resume rows by
// id). The builder is the source of truth: label follows the profile name and
// content is re-rendered on every save. Keyed by resumeProfileId so it's
// idempotent; cascade-deletes with the profile via the schema relation.
async function syncCompanionResume(
  id: string,
  name: string,
  profile: ResumeProfileData,
): Promise<void> {
  const label = name.trim() || "Untitled resume";
  const content = profileToText(profile) || label;
  await prisma.resume.upsert({
    where: { resumeProfileId: id },
    // On create the companion starts active so it's immediately pickable. On
    // update we deliberately DON'T touch isActive — the user may have toggled it
    // off (on /resumes or in the builder); a later save shouldn't re-activate it.
    create: {
      id: `builder-${id}`,
      resumeProfileId: id,
      label,
      content,
      isActive: true,
    },
    update: { label, content },
  });
  revalidatePath("/resumes");
}

// Toggle the active state of a builder resume's companion (controls whether it
// appears in the applications / reach-out / AI pickers). Called from the builder
// editor's active switch. Idempotent — upserts the companion if it's somehow
// missing so the toggle always has something to flip.
export async function setResumeProfileActive(
  id: string,
  isActive: boolean,
): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    const existing = await prisma.resume.findUnique({
      where: { resumeProfileId: id },
    });
    if (existing) {
      await prisma.resume.update({
        where: { resumeProfileId: id },
        data: { isActive },
      });
    } else {
      // No companion yet (e.g. a legacy profile not re-saved). Seed one so the
      // toggle reflects reality. Render from the stored profile.
      const rp = await prisma.resumeProfile.findUnique({ where: { id } });
      if (!rp) return { ok: false, error: "Resume not found." };
      const p = normalizeProfile({
        header: rp.header,
        education: rp.education,
        experience: rp.experience,
        skills: rp.skills,
        projects: rp.projects,
      });
      const label = rp.name.trim() || "Untitled resume";
      await prisma.resume.create({
        data: {
          id: `builder-${id}`,
          resumeProfileId: id,
          label,
          content: profileToText(p) || label,
          isActive,
        },
      });
    }
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/resumes");
  revalidatePath(`/resume-builder/${id}`);
  return { ok: true };
}

// Persist the JSON sections via Prisma. We normalize on the way in so a
// malformed payload (e.g. from an AI draft) can't poison the row.
// When `sourceId` is provided, clone that resume's sections into the new row
// (import from an existing resume). Missing/unknown sources fall back to blank.
export async function createResumeProfile(
  name: string,
  sourceId?: string,
): Promise<void> {
  const id = randomUUID();

  let sections: ResumeProfileData = emptyProfile();

  if (sourceId) {
    const src = await prisma.resumeProfile.findUnique({ where: { id: sourceId } });
    if (src) {
      // normalizeProfile guarantees a well-formed copy even from legacy rows.
      sections = normalizeProfile({
        header: src.header,
        education: src.education,
        experience: src.experience,
        skills: src.skills,
        projects: src.projects,
      });
    }
  }

  await prisma.resumeProfile.create({
    data: {
      id,
      name: name.trim() || "Untitled resume",
      ...sections,
    },
  });
  // Seed the companion so a brand-new builder resume is immediately pickable.
  await syncCompanionResume(id, name, sections);
  revalidatePath("/resume-builder");
  redirect(`/resume-builder/${id}`);
}

export async function saveResumeProfile(
  id: string,
  name: string,
  profile: ResumeProfileData,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const p = normalizeProfile(profile);
  try {
    await prisma.resumeProfile.update({
      where: { id },
      data: {
        name: name.trim() || "Untitled resume",
        header: p.header,
        education: p.education,
        experience: p.experience,
        skills: p.skills,
        projects: p.projects,
      },
    });
    await syncCompanionResume(id, name, p);
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/resume-builder");
  revalidatePath(`/resume-builder/${id}`);
  return { ok: true };
}

export async function deleteResumeProfile(id: string): Promise<void> {
  await prisma.resumeProfile.delete({ where: { id } });
  revalidatePath("/resume-builder");
  redirect("/resume-builder");
}
