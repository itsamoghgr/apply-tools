import Link from "next/link";
import { notFound } from "next/navigation";
import { prisma } from "@/lib/prisma";
import ResumeForm from "../ResumeForm";
import { deleteResume, updateResume } from "../actions";
import DeleteButton from "./DeleteButton";
import { ArrowLeft } from "lucide-react";

export const dynamic = "force-dynamic";

export default async function EditResumePage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const resume = await prisma.resume.findUnique({ where: { id } });
  if (!resume) notFound();

  const action = updateResume.bind(null, id);
  const del = deleteResume.bind(null, id);

  return (
    <div className="space-y-6 max-w-4xl animate-slide-up">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">
            {resume.label}
          </h1>
          <p className="text-xs opacity-40 mt-1 font-mono">{resume.id}</p>
        </div>
        <Link
          href="/resumes"
          className="text-sm opacity-60 hover:opacity-100 transition-opacity flex items-center gap-1.5"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </Link>
      </div>
      <div className="glass-card p-6 sm:p-8">
        <ResumeForm
          action={action}
          initial={{
            id: resume.id,
            label: resume.label,
            content: resume.content,
            isActive: resume.isActive,
          }}
          submitLabel="Save changes"
          successMessage="Saved"
        />
      </div>
      <div className="rounded-box border border-error/30 bg-error/5 backdrop-blur-sm"
           style={{ boxShadow: '0 0 16px -4px color-mix(in oklab, var(--color-error) 15%, transparent)' }}>
        <div className="p-5 flex-row flex items-center justify-between">
          <div>
            <div className="text-sm font-medium">Danger zone</div>
            <div className="text-xs opacity-60 mt-0.5">
              Deletes this resume from the database. History rows referencing
              it stay; their resume link becomes null.
            </div>
          </div>
          <DeleteButton action={del} label={resume.label} />
        </div>
      </div>
    </div>
  );
}
