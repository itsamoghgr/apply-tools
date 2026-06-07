"use server";

import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { prisma } from "@/lib/prisma";
import { normalizeProfile, type ResumeProfileData } from "./types";

// Persist the JSON sections via Prisma. We normalize on the way in so a
// malformed payload (e.g. from an AI draft) can't poison the row.
export async function createResumeProfile(name: string): Promise<void> {
  const id = randomUUID();
  await prisma.resumeProfile.create({
    data: {
      id,
      name: name.trim() || "Untitled resume",
      header: {},
      education: [],
      experience: [],
      skills: [],
      projects: [],
    },
  });
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
