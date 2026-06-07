-- CreateTable
CREATE TABLE "JobApplication" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "companyName" TEXT NOT NULL,
    "jobRole" TEXT,
    "location" TEXT,
    "interviewStatus" TEXT,
    "status" TEXT NOT NULL DEFAULT 'Applied',
    "appliedDate" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "resumeId" TEXT,
    "companyCareerPage" TEXT,
    "decisionDate" DATETIME,
    "decisionTime" TEXT,
    "notes" TEXT,
    "hrName" TEXT,
    "hrLinkedin" TEXT,
    "hrEmail" TEXT,
    "referral" TEXT,
    "referralLinkedin" TEXT,
    "jobDescription" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" DATETIME NOT NULL,
    CONSTRAINT "JobApplication_resumeId_fkey" FOREIGN KEY ("resumeId") REFERENCES "Resume" ("id") ON DELETE SET NULL ON UPDATE CASCADE
);

-- CreateIndex
CREATE INDEX "JobApplication_createdAt_idx" ON "JobApplication"("createdAt");

-- CreateIndex
CREATE INDEX "JobApplication_status_idx" ON "JobApplication"("status");
