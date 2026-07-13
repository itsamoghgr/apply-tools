import Link from "next/link";
import { prisma } from "@/lib/prisma";
import { FileText } from "lucide-react";
import NewResumeButton from "./NewResumeButton";

export const dynamic = "force-dynamic";

export default async function ResumeBuilderPage() {
  const resumes = await prisma.resumeProfile.findMany({
    orderBy: { updatedAt: "desc" },
    select: { id: true, name: true, updatedAt: true },
  });

  return (
    <div className="space-y-6 animate-slide-up">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Resume Builder</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {resumes.length}
          </span>
        </div>
        <NewResumeButton
          existing={resumes.map((r) => ({ id: r.id, name: r.name }))}
        />
      </div>

      {resumes.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-3">🧩</div>
          <p className="text-sm opacity-60">
            No resumes yet. Build one section by section, let AI sharpen the
            bullets, then export a polished PDF.
          </p>
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {resumes.map((r) => (
            <Link
              key={r.id}
              href={`/resume-builder/${r.id}`}
              className="glass-card p-5 hover:border-primary/40 transition-colors group"
            >
              <div className="flex items-start gap-3">
                <div className="rounded-lg bg-primary/15 text-primary p-2.5">
                  <FileText className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <div className="font-medium truncate group-hover:text-primary transition-colors">
                    {r.name}
                  </div>
                  <div className="text-xs opacity-50 mt-1">
                    Updated {r.updatedAt.toLocaleDateString()}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
