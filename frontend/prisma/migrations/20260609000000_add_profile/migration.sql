-- CreateTable: master Profile (singleton, id = "me")
CREATE TABLE "Profile" (
    "id"        TEXT NOT NULL,
    "fullName"  TEXT,
    "email"     TEXT,
    "phone"     TEXT,
    "location"  TEXT,
    "linkedin"  TEXT,
    "github"    TEXT,
    "portfolio" TEXT,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "Profile_pkey" PRIMARY KEY ("id")
);

-- CreateTable: work experiences belonging to a Profile
CREATE TABLE "Experience" (
    "id"        TEXT NOT NULL,
    "profileId" TEXT NOT NULL,
    "company"   TEXT NOT NULL,
    "title"     TEXT NOT NULL,
    "location"  TEXT,
    "startDate" TEXT,
    "endDate"   TEXT,
    "bullets"   TEXT[] DEFAULT ARRAY[]::TEXT[],
    "order"     INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "Experience_pkey" PRIMARY KEY ("id")
);

-- CreateTable: projects belonging to a Profile
CREATE TABLE "Project" (
    "id"        TEXT NOT NULL,
    "profileId" TEXT NOT NULL,
    "name"      TEXT NOT NULL,
    "date"      TEXT,
    "link"      TEXT,
    "bullets"   TEXT[] DEFAULT ARRAY[]::TEXT[],
    "order"     INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "Project_pkey" PRIMARY KEY ("id")
);

-- CreateTable: skill groups belonging to a Profile
CREATE TABLE "Skill" (
    "id"        TEXT NOT NULL,
    "profileId" TEXT NOT NULL,
    "category"  TEXT NOT NULL,
    "items"     TEXT[] DEFAULT ARRAY[]::TEXT[],
    "order"     INTEGER NOT NULL DEFAULT 0,

    CONSTRAINT "Skill_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "Experience_profileId_order_idx" ON "Experience"("profileId", "order");

-- CreateIndex
CREATE INDEX "Project_profileId_order_idx" ON "Project"("profileId", "order");

-- CreateIndex
CREATE INDEX "Skill_profileId_order_idx" ON "Skill"("profileId", "order");

-- AddForeignKey
ALTER TABLE "Experience" ADD CONSTRAINT "Experience_profileId_fkey"
    FOREIGN KEY ("profileId") REFERENCES "Profile"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Project" ADD CONSTRAINT "Project_profileId_fkey"
    FOREIGN KEY ("profileId") REFERENCES "Profile"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Skill" ADD CONSTRAINT "Skill_profileId_fkey"
    FOREIGN KEY ("profileId") REFERENCES "Profile"("id") ON DELETE CASCADE ON UPDATE CASCADE;
