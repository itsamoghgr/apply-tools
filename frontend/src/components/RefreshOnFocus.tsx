"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Re-fetches the current server component tree when the tab regains
 * focus or becomes visible. Lets the dashboard reflect changes made
 * in the extension popup without a manual reload.
 */
export default function RefreshOnFocus() {
  const router = useRouter();

  useEffect(() => {
    const refresh = () => router.refresh();

    const onVisibility = () => {
      if (document.visibilityState === "visible") refresh();
    };

    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [router]);

  return null;
}
