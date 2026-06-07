-- CreateTable
CREATE TABLE "JobApplicationLead" (
    "jobApplicationId" TEXT NOT NULL,
    "leadId" TEXT NOT NULL,
    "role" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY ("jobApplicationId", "leadId"),
    CONSTRAINT "JobApplicationLead_jobApplicationId_fkey" FOREIGN KEY ("jobApplicationId") REFERENCES "JobApplication" ("id") ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT "JobApplicationLead_leadId_fkey" FOREIGN KEY ("leadId") REFERENCES "Lead" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- RedefineTables
PRAGMA defer_foreign_keys=ON;
PRAGMA foreign_keys=OFF;
CREATE TABLE "new_ReachOut" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "recipientName" TEXT NOT NULL,
    "recipientEmail" TEXT NOT NULL,
    "linkedinProfile" TEXT NOT NULL,
    "contextNote" TEXT,
    "resumeId" TEXT,
    "leadId" TEXT,
    "subject" TEXT NOT NULL,
    "body" TEXT NOT NULL,
    "htmlBody" TEXT,
    "status" TEXT NOT NULL DEFAULT 'draft',
    "sentAt" DATETIME,
    "errorMessage" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    "jobApplicationId" TEXT,
    CONSTRAINT "ReachOut_resumeId_fkey" FOREIGN KEY ("resumeId") REFERENCES "Resume" ("id") ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT "ReachOut_leadId_fkey" FOREIGN KEY ("leadId") REFERENCES "Lead" ("id") ON DELETE SET NULL ON UPDATE CASCADE,
    CONSTRAINT "ReachOut_jobApplicationId_fkey" FOREIGN KEY ("jobApplicationId") REFERENCES "JobApplication" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);
INSERT INTO "new_ReachOut" ("body", "contextNote", "createdAt", "errorMessage", "htmlBody", "id", "leadId", "linkedinProfile", "recipientEmail", "recipientName", "resumeId", "sentAt", "status", "subject", "updatedAt") SELECT "body", "contextNote", "createdAt", "errorMessage", "htmlBody", "id", "leadId", "linkedinProfile", "recipientEmail", "recipientName", "resumeId", "sentAt", "status", "subject", "updatedAt" FROM "ReachOut";
DROP TABLE "ReachOut";
ALTER TABLE "new_ReachOut" RENAME TO "ReachOut";
CREATE INDEX "ReachOut_createdAt_idx" ON "ReachOut"("createdAt");
CREATE INDEX "ReachOut_status_idx" ON "ReachOut"("status");
CREATE INDEX "ReachOut_leadId_idx" ON "ReachOut"("leadId");
CREATE INDEX "ReachOut_jobApplicationId_idx" ON "ReachOut"("jobApplicationId");
PRAGMA foreign_keys=ON;
PRAGMA defer_foreign_keys=OFF;

-- CreateIndex
CREATE INDEX "JobApplicationLead_leadId_idx" ON "JobApplicationLead"("leadId");
