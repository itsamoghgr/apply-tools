import Link from "next/link";
import { prisma } from "@/lib/prisma";
import ResumeTable from "./ResumeTable";
import { Plus } from "lucide-react";

export const dynamic = "force-dynamic";

export default async function ResumesPage() {
  const resumes = await prisma.resume.findMany({
    orderBy: { id: "asc" },
    select: { id: true, label: true, isActive: true, updatedAt: true },
  });

  return (
    <div className="space-y-6 animate-slide-up">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Resumes</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {resumes.length}
          </span>
        </div>
        <Link href="/resumes/new" className="btn btn-gradient btn-sm gap-2">
          <Plus className="h-4 w-4" />
          New resume
        </Link>
      </div>

      {resumes.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-3">📄</div>
          <p className="text-sm opacity-60">
            No resumes yet. Create one to get started.
          </p>
        </div>
      ) : (
        <ResumeTable
          resumes={resumes.map((r) => ({
            ...r,
            updatedAt: r.updatedAt.toISOString(),
          }))}
        />
      )}
    </div>
  );
}
