import { PrismaClient } from "@prisma/client";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";

const prisma = new PrismaClient();

const RESUMES_DIR = resolve(__dirname, "../../backend/resumes");

function parseResumeFile(filepath: string, idSlug: string) {
  const raw = readFileSync(filepath, "utf-8");
  const lines = raw.split("\n");
  let label = idSlug
    .split(/[-_]/)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ");
  let content = raw;

  if (lines[0]?.toLowerCase().startsWith("# label:")) {
    const labelFromHeader = lines[0].split(":").slice(1).join(":").trim();
    if (labelFromHeader) label = labelFromHeader;
    content = lines.slice(1).join("\n").replace(/^\n+/, "");
  }
  return { label, content };
}

async function main() {
  let entries: string[] = [];
  try {
    entries = readdirSync(RESUMES_DIR);
  } catch {
    console.warn(`No resumes dir at ${RESUMES_DIR} — nothing to seed.`);
    return;
  }

  const txtFiles = entries.filter(
    (f) => f.endsWith(".txt") && statSync(join(RESUMES_DIR, f)).isFile(),
  );

  for (const filename of txtFiles) {
    const id = filename.replace(/\.txt$/, "");
    const { label, content } = parseResumeFile(join(RESUMES_DIR, filename), id);

    await prisma.resume.upsert({
      where: { id },
      update: { label, content },
      create: { id, label, content, isActive: true },
    });
    console.log(`  seeded ${id} (${label})`);
  }

  const total = await prisma.resume.count();
  console.log(`Seed complete. Total resumes: ${total}`);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
