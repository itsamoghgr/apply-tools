"use server";

import { randomUUID } from "node:crypto";
import { revalidatePath } from "next/cache";
import { prisma } from "@/lib/prisma";
import {
  normalizeExperience,
  normalizeHeader,
  normalizeProject,
  normalizeSkill,
  type ExperiencePayload,
  type ProfileData,
  type ProjectPayload,
  type SkillPayload,
} from "./types";

// Fixed id: the master profile is a singleton for this user.
const PROFILE_ID = "me";

type MutationResult = { ok: true } | { ok: false; error: string };

// ── Profile ───────────────────────────────────────────────────────────────────

// Returns the singleton master profile, creating it if it doesn't exist yet.
// Child rows are included and sorted by their `order` field.
export async function getProfile(): Promise<ProfileData> {
  const profile = await prisma.profile.upsert({
    where: { id: PROFILE_ID },
    create: { id: PROFILE_ID },
    update: {},
    include: {
      experiences: { orderBy: { order: "asc" } },
      projects: { orderBy: { order: "asc" } },
      skills: { orderBy: { order: "asc" } },
    },
  });

  return {
    id: profile.id,
    fullName: profile.fullName ?? "",
    email: profile.email ?? "",
    phone: profile.phone ?? "",
    location: profile.location ?? "",
    linkedin: profile.linkedin ?? "",
    github: profile.github ?? "",
    portfolio: profile.portfolio ?? "",
    experiences: profile.experiences.map((e) => ({
      id: e.id,
      order: e.order,
      company: e.company,
      title: e.title,
      location: e.location ?? "",
      startDate: e.startDate ?? "",
      endDate: e.endDate ?? "",
      bullets: e.bullets,
    })),
    projects: profile.projects.map((p) => ({
      id: p.id,
      order: p.order,
      name: p.name,
      date: p.date ?? "",
      link: p.link ?? "",
      bullets: p.bullets,
    })),
    skills: profile.skills.map((s) => ({
      id: s.id,
      order: s.order,
      category: s.category,
      items: s.items,
    })),
  };
}

// Updates the header fields of the master profile.
export async function saveProfileHeader(
  data: unknown,
): Promise<MutationResult> {
  const h = normalizeHeader(data);
  try {
    await prisma.profile.upsert({
      where: { id: PROFILE_ID },
      create: { id: PROFILE_ID, ...h },
      update: h,
    });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// ── Experience CRUD ───────────────────────────────────────────────────────────

export async function addExperience(
  data: unknown,
): Promise<MutationResult> {
  const payload = normalizeExperience(data);
  try {
    const agg = await prisma.experience.aggregate({
      where: { profileId: PROFILE_ID },
      _max: { order: true },
    });
    const nextOrder = (agg._max.order ?? -1) + 1;
    await prisma.experience.create({
      data: { id: randomUUID(), profileId: PROFILE_ID, order: nextOrder, ...payload },
    });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function updateExperience(
  id: string,
  data: unknown,
): Promise<MutationResult> {
  const payload = normalizeExperience(data);
  try {
    await prisma.experience.update({ where: { id }, data: payload });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function deleteExperience(id: string): Promise<MutationResult> {
  try {
    await prisma.experience.delete({ where: { id } });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// Persists a new ordering for all experiences. `ids` must be a complete ordered
// list of experience ids belonging to the profile.
export async function reorderExperiences(
  ids: string[],
): Promise<MutationResult> {
  try {
    await prisma.$transaction(
      ids.map((id, index) =>
        prisma.experience.update({ where: { id }, data: { order: index } }),
      ),
    );
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// ── Project CRUD ──────────────────────────────────────────────────────────────

export async function addProject(data: unknown): Promise<MutationResult> {
  const payload = normalizeProject(data);
  try {
    const agg = await prisma.project.aggregate({
      where: { profileId: PROFILE_ID },
      _max: { order: true },
    });
    const nextOrder = (agg._max.order ?? -1) + 1;
    await prisma.project.create({
      data: { id: randomUUID(), profileId: PROFILE_ID, order: nextOrder, ...payload },
    });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function updateProject(
  id: string,
  data: unknown,
): Promise<MutationResult> {
  const payload = normalizeProject(data);
  try {
    await prisma.project.update({ where: { id }, data: payload });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function deleteProject(id: string): Promise<MutationResult> {
  try {
    await prisma.project.delete({ where: { id } });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// Persists a new ordering for all projects.
export async function reorderProjects(ids: string[]): Promise<MutationResult> {
  try {
    await prisma.$transaction(
      ids.map((id, index) =>
        prisma.project.update({ where: { id }, data: { order: index } }),
      ),
    );
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// ── Skill CRUD ────────────────────────────────────────────────────────────────

export async function addSkill(data: unknown): Promise<MutationResult> {
  const payload = normalizeSkill(data);
  try {
    const agg = await prisma.skill.aggregate({
      where: { profileId: PROFILE_ID },
      _max: { order: true },
    });
    const nextOrder = (agg._max.order ?? -1) + 1;
    await prisma.skill.create({
      data: { id: randomUUID(), profileId: PROFILE_ID, order: nextOrder, ...payload },
    });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function updateSkill(
  id: string,
  data: unknown,
): Promise<MutationResult> {
  const payload = normalizeSkill(data);
  try {
    await prisma.skill.update({ where: { id }, data: payload });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

export async function deleteSkill(id: string): Promise<MutationResult> {
  try {
    await prisma.skill.delete({ where: { id } });
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}

// Persists a new ordering for all skills.
export async function reorderSkills(ids: string[]): Promise<MutationResult> {
  try {
    await prisma.$transaction(
      ids.map((id, index) =>
        prisma.skill.update({ where: { id }, data: { order: index } }),
      ),
    );
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  revalidatePath("/profile");
  return { ok: true };
}
