import Link from "next/link";
import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import HistoryRow from "./HistoryRow";

export const dynamic = "force-dynamic";

const MODES = ["all", "cover_letter", "email", "outreach", "score"] as const;
type ModeFilter = (typeof MODES)[number];

export default async function HistoryPage({
  searchParams,
}: {
  searchParams: Promise<{ mode?: string }>;
}) {
  const sp = await searchParams;
  const mode: ModeFilter = (MODES as readonly string[]).includes(sp.mode ?? "all")
    ? (sp.mode as ModeFilter)
    : "all";

  const apps = await prisma.application.findMany({
    where: mode === "all" ? {} : { mode },
    orderBy: { createdAt: "desc" },
    take: 200,
    include: { resume: { select: { id: true, label: true } } },
  });

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <h1 className="text-3xl font-bold tracking-tight">History</h1>
          <span className="badge badge-primary font-mono tabular-nums px-3 py-1 text-sm">
            {apps.length}
          </span>
        </div>
        <div role="tablist" className="tabs tabs-bordered flex-wrap">
          {MODES.map((m) => (
            <Link
              key={m}
              role="tab"
              href={m === "all" ? "/history" : `/history?mode=${m}`}
              className={`tab text-xs transition-colors ${
                m === mode
                  ? "tab-active text-primary font-medium"
                  : "opacity-70 hover:opacity-100"
              }`}
            >
              {m === "all" ? "All" : m.replace("_", " ")}
            </Link>
          ))}
        </div>
      </div>

      {apps.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="text-4xl mb-3">📜</div>
          <p className="text-sm opacity-60">
            No applications yet — generate something to see it here.
          </p>
        </div>
      ) : (
        <div className="glass-card overflow-hidden">
          <table className="table table-sm">
            <thead>
              <tr className="border-b border-base-300/40">
                <th>When</th>
                <th>Mode</th>
                <th>Company</th>
                <th>Resume</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {apps.map((a) => (
                <HistoryRow
                  key={a.id}
                  app={{
                    id: a.id,
                    mode: a.mode,
                    company: a.company,
                    resumeLabel: a.resume?.label ?? a.resumeId ?? "—",
                    createdAt: a.createdAt.toISOString(),
                    output: a.output,
                    pdfPath: a.pdfPath,
                  }}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
