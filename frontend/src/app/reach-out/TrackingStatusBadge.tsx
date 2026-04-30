import { Activity, AlertTriangle } from "lucide-react";

type Props = {
  ready: boolean;
  publicUrl: string | null;
};

function hostnameOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}

export default function TrackingStatusBadge({ ready, publicUrl }: Props) {
  if (ready && publicUrl) {
    const host = hostnameOf(publicUrl);
    return (
      <div
        className="inline-flex items-center gap-2 rounded-full border border-success/30 bg-success/10 px-3 py-1 text-xs"
        title={`Tracking sidecar reachable at ${publicUrl}`}
      >
        <Activity className="h-3.5 w-3.5 text-success" />
        <span className="font-medium">Tracking</span>
        {host ? <span className="opacity-60 font-mono">{host}</span> : null}
      </div>
    );
  }

  return (
    <div
      className="inline-flex items-center gap-2 rounded-full border border-warning/30 bg-warning/10 px-3 py-1 text-xs"
      title="Deploy the tracking-sidecar service and set TRACKING_BASE_URL, TRACKING_FERNET_KEY, TRACKING_API_TOKEN in backend/.env. See tracking-sidecar/README.md."
    >
      <AlertTriangle className="h-3.5 w-3.5 text-warning" />
      <span className="font-medium">Tracking offline</span>
      <span className="opacity-60">deploy sidecar</span>
    </div>
  );
}
