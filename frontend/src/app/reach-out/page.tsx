import { prisma } from "@/lib/prisma";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import GmailSettingsCard from "./GmailSettingsCard";
import ReachOutComposer from "./ReachOutComposer";
import ReachOutList from "./ReachOutList";
import TrackingStatusBadge from "./TrackingStatusBadge";

export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";

type Aggregate = {
  openCount: number;
  clickCount: number;
  lastOpenedAt: string | null;
  lastClickedAt: string | null;
};

async function fetchTrackingStatus(): Promise<{
  publicUrl: string | null;
  ready: boolean;
}> {
  try {
    const res = await fetch(`${BACKEND_URL}/settings/tracking`, {
      cache: "no-store",
    });
    if (!res.ok) throw new Error(`status ${res.status}`);
    const json = (await res.json()) as { publicUrl: string | null; ready: boolean };
    return { publicUrl: json.publicUrl ?? null, ready: Boolean(json.ready) };
  } catch {
    return { publicUrl: null, ready: false };
  }
}

async function fetchAggregates(ids: string[]): Promise<Record<string, Aggregate>> {
  // The backend proxies this to the tracking sidecar. We tolerate failure
  // here so a sleeping Render free-tier instance doesn't break the
  // dashboard — the list view just shows zero counters until the sidecar
  // wakes up.
  if (ids.length === 0) return {};
  try {
    const res = await fetch(`${BACKEND_URL}/reach-out/aggregates`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ids }),
      cache: "no-store",
    });
    if (!res.ok) return {};
    const json = (await res.json()) as { aggregates?: Record<string, Aggregate> };
    return json.aggregates ?? {};
  } catch {
    return {};
  }
}

export default async function ReachOutPage() {
  const [
    resumes,
    reachOutsRaw,
    gmailAddressRow,
    gmailPasswordRow,
    fromNameRow,
    trackingStatus,
  ] = await Promise.all([
    prisma.resume.findMany({
      where: { isActive: true },
      orderBy: { id: "asc" },
      select: { id: true, label: true },
    }),
    prisma.reachOut.findMany({
      orderBy: { createdAt: "desc" },
      take: 50,
    }),
    prisma.setting.findUnique({ where: { key: "gmail_address" } }),
    prisma.setting.findUnique({ where: { key: "gmail_app_password" } }),
    prisma.setting.findUnique({ where: { key: "gmail_from_name" } }),
    fetchTrackingStatus(),
  ]);

  const reachOutIds = reachOutsRaw.map((r) => r.id);
  // Only ask the sidecar for aggregates when at least one row is sent,
  // and skip entirely when tracking isn't configured (sidecar unreachable).
  const aggregates =
    trackingStatus.ready && reachOutsRaw.some((r) => r.status === "sent")
      ? await fetchAggregates(reachOutIds)
      : {};

  const reachOuts = reachOutsRaw.map((r) => {
    const agg = aggregates[r.id];
    return {
      id: r.id,
      recipientName: r.recipientName,
      recipientEmail: r.recipientEmail,
      linkedinProfile: r.linkedinProfile,
      contextNote: r.contextNote,
      resumeId: r.resumeId,
      subject: r.subject,
      body: r.body,
      status: r.status,
      sentAt: r.sentAt?.toISOString() ?? null,
      errorMessage: r.errorMessage,
      openCount: agg?.openCount ?? 0,
      clickCount: agg?.clickCount ?? 0,
      lastOpenedAt: agg?.lastOpenedAt ?? null,
      lastClickedAt: agg?.lastClickedAt ?? null,
      createdAt: r.createdAt.toISOString(),
    };
  });

  const gmail = {
    address: gmailAddressRow?.value ?? null,
    fromName: fromNameRow?.value ?? null,
    hasPassword: Boolean(gmailPasswordRow?.value),
  };

  return (
    <div className="space-y-6 animate-slide-up">
      <RefreshOnFocus />

      <div className="flex items-end justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Reach Out</h1>
          <p className="text-sm opacity-60 mt-1">
            Draft a personalized email from a LinkedIn profile, preview, then
            send from your Gmail.
          </p>
        </div>
        <TrackingStatusBadge
          ready={trackingStatus.ready}
          publicUrl={trackingStatus.publicUrl}
        />
      </div>

      <GmailSettingsCard initial={gmail} />

      <ReachOutComposer
        resumes={resumes}
        gmailConnected={Boolean(gmail.address && gmail.hasPassword)}
        trackingReady={trackingStatus.ready}
      />

      <ReachOutList initial={reachOuts} />
    </div>
  );
}
