import { NextRequest, NextResponse } from "next/server";
import { readFile, stat } from "node:fs/promises";
import { resolve } from "node:path";
import { prisma } from "@/lib/prisma";

export const dynamic = "force-dynamic";

const PDF_ROOT = resolve(process.cwd(), "..", "data", "pdfs");

export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "id required" }, { status: 400 });

  const app = await prisma.application.findUnique({
    where: { id },
    select: { pdfPath: true, company: true },
  });
  if (!app?.pdfPath) {
    return NextResponse.json({ error: "no PDF for this application" }, { status: 404 });
  }

  const abs = resolve(app.pdfPath);
  if (!abs.startsWith(PDF_ROOT + "/") && abs !== PDF_ROOT) {
    return NextResponse.json({ error: "path outside PDF root" }, { status: 403 });
  }

  try {
    await stat(abs);
  } catch {
    return NextResponse.json({ error: "file missing" }, { status: 404 });
  }

  const buf = await readFile(abs);
  const filename = `CoverLetter_${app.company ?? "Company"}.pdf`;
  return new NextResponse(new Uint8Array(buf), {
    status: 200,
    headers: {
      "content-type": "application/pdf",
      "content-disposition": `inline; filename="${filename}"`,
    },
  });
}
