"use client";

import { useState } from "react";
import { Download } from "lucide-react";

type App = {
  id: string;
  mode: string;
  company: string | null;
  resumeLabel: string;
  createdAt: string;
  output: string | null;
  pdfPath: string | null;
};

const MODE_BADGE: Record<string, string> = {
  cover_letter: "bg-indigo-500/15 text-indigo-400 border-indigo-500/25",
  email: "bg-cyan-500/15 text-cyan-400 border-cyan-500/25",
  outreach: "bg-purple-500/15 text-purple-400 border-purple-500/25",
  score: "bg-emerald-500/15 text-emerald-400 border-emerald-500/25",
  answer_question: "bg-orange-500/15 text-orange-400 border-orange-500/25",
};

function prettyOutput(mode: string, raw: string | null): string {
  if (!raw) return "(no text output)";
  try {
    const parsed = JSON.parse(raw);
    if (mode === "email") return `Subject: ${parsed.subject}\n\n${parsed.body}`;
    if (mode === "outreach") {
      const sub = parsed.subject ? `Subject: ${parsed.subject}\n\n` : "";
      return `${sub}${parsed.message} (${parsed.char_count} chars)`;
    }
    return JSON.stringify(parsed, null, 2);
  } catch {
    return raw;
  }
}

export default function HistoryRow({ app }: { app: App }) {
  const [open, setOpen] = useState(false);
  const modeBadge = MODE_BADGE[app.mode] ?? "bg-base-300/30 text-base-content/70 border-base-300";

  return (
    <>
      <tr className="hover:bg-base-200/40 transition-colors">
        <td className="opacity-50 whitespace-nowrap text-xs tabular-nums">
          {new Date(app.createdAt).toLocaleString()}
        </td>
        <td>
          <span
            className={`inline-flex items-center px-2 py-0.5 rounded-md text-[10px] uppercase tracking-wider font-medium border ${modeBadge}`}
          >
            {app.mode.replace("_", " ")}
          </span>
        </td>
        <td className="font-medium">{app.company ?? "—"}</td>
        <td className="opacity-60">{app.resumeLabel}</td>
        <td className="text-right whitespace-nowrap">
          {app.pdfPath && (
            <a
              href={`/api/pdf?id=${app.id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-ghost btn-xs gap-1 mr-2"
              title="Download PDF"
            >
              <Download className="h-3 w-3" />
              PDF
            </a>
          )}
          <button
            onClick={() => setOpen((o) => !o)}
            className="btn btn-ghost btn-xs"
          >
            {open ? "Hide" : "View"}
          </button>
        </td>
      </tr>
      {open && (
        <tr className="bg-base-200/30">
          <td colSpan={5} className="px-5 py-4">
            <div className="glass-card p-4 animate-fade-in">
              <pre className="text-xs whitespace-pre-wrap font-mono leading-relaxed opacity-80">
                {prettyOutput(app.mode, app.output)}
              </pre>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
