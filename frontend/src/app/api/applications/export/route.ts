import { NextRequest, NextResponse } from "next/server";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

// Statuses the Applications page exposes as tabs. We accept the same values
// here so "Export CSV" can mirror whatever filter the user is looking at.
const STATUSES = [
  "Applied",
  "In-Progress",
  "Offer",
  "Rejected",
  "Withdrawn",
  "Ghosted",
] as const;

// Columns exported, in order. Header label → row value extractor. This is the
// full JobApplication table (every scalar column) plus the resume label, so
// the CSV is a complete dump rather than the trimmed set the table renders.
const COLUMNS: {
  header: string;
  value: (a: ExportRow) => string | null | undefined;
}[] = [
  { header: "ID", value: (a) => a.id },
  { header: "Company", value: (a) => a.companyName },
  { header: "Role", value: (a) => a.jobRole },
  { header: "Job URL", value: (a) => a.jobUrl },
  { header: "Location", value: (a) => a.location },
  { header: "Interview Status", value: (a) => a.interviewStatus },
  { header: "Status", value: (a) => a.status },
  { header: "Applied Date", value: (a) => isoDate(a.appliedDate) },
  { header: "Resume", value: (a) => a.resume?.label },
  { header: "Company Career Page", value: (a) => a.companyCareerPage },
  { header: "Decision Date", value: (a) => isoDate(a.decisionDate) },
  { header: "Decision Time", value: (a) => a.decisionTime },
  { header: "Notes", value: (a) => a.notes },
  { header: "HR Name", value: (a) => a.hrName },
  { header: "HR LinkedIn", value: (a) => a.hrLinkedin },
  { header: "HR Email", value: (a) => a.hrEmail },
  { header: "Referral", value: (a) => a.referral },
  { header: "Referral LinkedIn", value: (a) => a.referralLinkedin },
  { header: "Job Description", value: (a) => a.jobDescription },
  { header: "Created At", value: (a) => isoDateTime(a.createdAt) },
  { header: "Updated At", value: (a) => isoDateTime(a.updatedAt) },
];

type ExportRow = {
  id: string;
  companyName: string;
  jobRole: string | null;
  jobUrl: string | null;
  location: string | null;
  interviewStatus: string | null;
  status: string;
  appliedDate: Date;
  resume: { label: string } | null;
  companyCareerPage: string | null;
  decisionDate: Date | null;
  decisionTime: string | null;
  notes: string | null;
  hrName: string | null;
  hrLinkedin: string | null;
  hrEmail: string | null;
  referral: string | null;
  referralLinkedin: string | null;
  jobDescription: string | null;
  createdAt: Date;
  updatedAt: Date;
};

function isoDate(d: Date | null): string {
  // appliedDate/decisionDate are calendar dates stored as midnight-UTC; slice
  // to YYYY-MM-DD so the CSV matches the day the user picked (see the table's
  // timezone note for why we read the UTC date).
  return d ? d.toISOString().slice(0, 10) : "";
}

function isoDateTime(d: Date | null): string {
  return d ? d.toISOString() : "";
}

// Quote a field per RFC 4180: wrap in double quotes when it contains a comma,
// quote, or newline, and escape embedded quotes by doubling them.
function csvCell(value: string | null | undefined): string {
  const s = value == null ? "" : String(value);
  if (/[",\r\n]/.test(s)) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export async function GET(req: NextRequest) {
  const statusParam = req.nextUrl.searchParams.get("status");
  const status = (STATUSES as readonly string[]).includes(statusParam ?? "")
    ? statusParam!
    : null;

  const rows = await prisma.jobApplication.findMany({
    where: status ? { status } : {},
    orderBy: [{ appliedDate: "desc" }, { createdAt: "desc" }],
    include: { resume: { select: { label: true } } },
  });

  const headerLine = COLUMNS.map((c) => csvCell(c.header)).join(",");
  const body = rows
    .map((r) => COLUMNS.map((c) => csvCell(c.value(r as ExportRow))).join(","))
    .join("\r\n");
  // Prepend a UTF-8 BOM so Excel reads non-ASCII characters correctly.
  const csv = "﻿" + headerLine + "\r\n" + body + "\r\n";

  const stamp = new Date().toISOString().slice(0, 10);
  const filename = status
    ? `applications_${status}_${stamp}.csv`
    : `applications_${stamp}.csv`;

  return new NextResponse(csv, {
    status: 200,
    headers: {
      "content-type": "text/csv; charset=utf-8",
      "content-disposition": `attachment; filename="${filename}"`,
    },
  });
}
