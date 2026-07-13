import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { prisma } from "@/lib/prisma";
import { normalizeProfile } from "../types";
import ResumeBuilderEditor from "../ResumeBuilderEditor";

export const dynamic = "force-dynamic";

export default async function EditResumeProfilePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const row = await prisma.resumeProfile.findUnique({
    where: { id },
    include: { resume: { select: { isActive: true } } },
  });
  if (!row) notFound();

  const profile = normalizeProfile({
    header: row.header,
    summary: row.summary,
    education: row.education,
    experience: row.experience,
    skills: row.skills,
    projects: row.projects,
    sectionOrder: row.sectionOrder,
  });

  // Whether this resume shows in the applications / reach-out / AI pickers.
  // No companion yet (legacy profile) reads as inactive until toggled/saved.
  const initialActive = row.resume?.isActive ?? false;

  return (
    <div className="space-y-6 animate-slide-up">
      <Link
        href="/resume-builder"
        className="text-sm opacity-60 hover:opacity-100 transition-opacity inline-flex items-center gap-1.5"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        All resumes
      </Link>
      <ResumeBuilderEditor
        id={id}
        initialName={row.name}
        initialProfile={profile}
        initialActive={initialActive}
      />
    </div>
  );
}
