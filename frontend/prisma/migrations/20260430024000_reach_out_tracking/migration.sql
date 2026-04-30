-- AlterTable
ALTER TABLE "ReachOut" ADD COLUMN "htmlBody" TEXT;
ALTER TABLE "ReachOut" ADD COLUMN "openCount" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "ReachOut" ADD COLUMN "clickCount" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE "ReachOut" ADD COLUMN "lastOpenedAt" DATETIME;
ALTER TABLE "ReachOut" ADD COLUMN "lastClickedAt" DATETIME;

-- CreateTable
CREATE TABLE "ReachOutEvent" (
    "id" TEXT NOT NULL PRIMARY KEY,
    "reachOutId" TEXT NOT NULL,
    "eventType" TEXT NOT NULL,
    "trackedUrl" TEXT,
    "userAgent" TEXT,
    "userIp" TEXT,
    "createdAt" DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT "ReachOutEvent_reachOutId_fkey" FOREIGN KEY ("reachOutId") REFERENCES "ReachOut" ("id") ON DELETE CASCADE ON UPDATE CASCADE
);

-- CreateIndex
CREATE INDEX "ReachOutEvent_reachOutId_createdAt_idx" ON "ReachOutEvent"("reachOutId", "createdAt");
