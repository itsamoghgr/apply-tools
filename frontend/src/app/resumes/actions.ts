"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { z } from "zod";
import { prisma } from "@/lib/prisma";

const RESUME_ID_RE = /^[a-z0-9_-]+$/;

const ResumeInput = z.object({
  id: z
    .string()
    .min(1)
    .max(64)
    .regex(RESUME_ID_RE, "Use lowercase letters, digits, hyphens, underscores"),
  label: z.string().min(1).max(200),
  content: z.string().min(1).max(200_000),
  isActive: z.boolean(),
});

export type FormState = { error?: string; ok?: boolean };

export async function createResume(
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const parsed = ResumeInput.safeParse({
    id: formData.get("id"),
    label: formData.get("label"),
    content: formData.get("content"),
    isActive: formData.get("isActive") === "on",
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
  }
  const exists = await prisma.resume.findUnique({ where: { id: parsed.data.id } });
  if (exists) return { error: `Resume id "${parsed.data.id}" already exists.` };
  await prisma.resume.create({ data: parsed.data });
  revalidatePath("/resumes");
  redirect(`/resumes/${parsed.data.id}`);
}

export async function updateResume(
  id: string,
  _prev: FormState,
  formData: FormData,
): Promise<FormState> {
  const parsed = ResumeInput.omit({ id: true }).safeParse({
    label: formData.get("label"),
    content: formData.get("content"),
    isActive: formData.get("isActive") === "on",
  });
  if (!parsed.success) {
    return { error: parsed.error.issues[0]?.message ?? "Invalid input" };
  }
  await prisma.resume.update({ where: { id }, data: parsed.data });
  revalidatePath("/resumes");
  revalidatePath(`/resumes/${id}`);
  return { ok: true };
}

export async function deleteResume(id: string): Promise<void> {
  await prisma.resume.delete({ where: { id } });
  revalidatePath("/resumes");
  redirect("/resumes");
}

export async function bulkSetActive(
  ids: string[],
  isActive: boolean,
): Promise<{ updated: number; error?: string }> {
  if (!Array.isArray(ids) || ids.length === 0) {
    return { updated: 0, error: "No resumes selected." };
  }
  // Defensive: validate each id matches the slug regex so we don't pass
  // anything weird into a where-in clause.
  const safe = ids.filter((id) => typeof id === "string" && RESUME_ID_RE.test(id));
  if (safe.length === 0) {
    return { updated: 0, error: "No valid resume ids in selection." };
  }
  const result = await prisma.resume.updateMany({
    where: { id: { in: safe } },
    data: { isActive },
  });
  revalidatePath("/resumes");
  return { updated: result.count };
}
