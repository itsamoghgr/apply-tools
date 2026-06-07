"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";

// Cooldown between refreshes. Tab-switching can fire focus + visibilitychange
// in quick succession (and macOS Mission Control fires them on every workspace
// flip), so without throttling a heavy server component like /applications
// re-runs its full Prisma query several times a minute for no user gain.
const REFRESH_COOLDOWN_MS = 30_000;

// Don't refresh for tiny away-blips (clicking another window for a second).
// Only refresh on visibility if the tab was hidden for at least this long.
const MIN_HIDDEN_MS = 5_000;

/**
 * Re-fetches the current server component tree when the tab regains focus
 * — but at most once per 30s, and only after a non-trivial hidden interval.
 * Lets the dashboard reflect changes made in the extension popup without
 * a manual reload, without re-running heavy queries on every alt-tab.
 */
export default function RefreshOnFocus() {
  const router = useRouter();
  const lastRefreshRef = useRef(0);
  const hiddenSinceRef = useRef<number | null>(null);

  useEffect(() => {
    const maybeRefresh = () => {
      const now = Date.now();
      if (now - lastRefreshRef.current < REFRESH_COOLDOWN_MS) return;
      lastRefreshRef.current = now;
      router.refresh();
    };

    const onFocus = () => maybeRefresh();

    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        hiddenSinceRef.current = Date.now();
        return;
      }
      const hiddenSince = hiddenSinceRef.current;
      hiddenSinceRef.current = null;
      if (hiddenSince && Date.now() - hiddenSince < MIN_HIDDEN_MS) return;
      maybeRefresh();
    };

    window.addEventListener("focus", onFocus);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [router]);

  return null;
}
