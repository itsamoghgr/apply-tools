-- DropTable
DROP TABLE IF EXISTS "ReachOutEvent";

-- AlterTable: drop tracking aggregate columns. Counts now live in the
-- tracking-sidecar's Postgres and are fetched on demand.
ALTER TABLE "ReachOut" DROP COLUMN "openCount";
ALTER TABLE "ReachOut" DROP COLUMN "clickCount";
ALTER TABLE "ReachOut" DROP COLUMN "lastOpenedAt";
ALTER TABLE "ReachOut" DROP COLUMN "lastClickedAt";
