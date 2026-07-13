import Link from "next/link";
import RefreshOnFocus from "@/components/RefreshOnFocus";
import MailClient from "./MailClient";

export const dynamic = "force-dynamic";

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8001";

type MailMessage = {
  id: string;
  messageId?: string;
  fromName: string;
  fromEmail: string;
  to: string;
  subject: string;
  date: string | null;
  snippet: string;
  unread: boolean;
};

type MailResponse = {
  configured: boolean;
  address: string | null;
  messages: MailMessage[];
};

async function fetchMail(): Promise<{
  data: MailResponse;
  error: string | null;
}> {
  try {
    const res = await fetch(`${BACKEND_URL}/mail?limit=50`, {
      cache: "no-store",
    });
    if (!res.ok) {
      let detail = `status ${res.status}`;
      try {
        const json = (await res.json()) as { detail?: string };
        if (json.detail) detail = json.detail;
      } catch {}
      return {
        data: { configured: true, address: null, messages: [] },
        error: detail,
      };
    }
    const json = (await res.json()) as MailResponse;
    return { data: json, error: null };
  } catch (e) {
    return {
      data: { configured: true, address: null, messages: [] },
      error: e instanceof Error ? e.message : "Could not reach the backend.",
    };
  }
}

export default async function MailPage() {
  const { data, error } = await fetchMail();

  if (!data.configured) {
    return (
      <div className="space-y-6 animate-slide-up">
        <RefreshOnFocus />
        <div>
          <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">Mail</h1>
          <p className="text-sm opacity-60 mt-1">
            Latest messages from your Gmail inbox.
          </p>
        </div>
        <div className="rounded-lg border border-base-300/40 bg-base-200/40 p-6">
          <h2 className="font-medium">Connect Gmail to see your inbox</h2>
          <p className="text-sm opacity-70 mt-1">
            Add your Gmail address and an app password under{" "}
            <Link href="/reach-out" className="link link-primary">
              Reach Out → Gmail settings
            </Link>
            . The same credentials are used to read your inbox.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-slide-up">
      <RefreshOnFocus />
      <MailClient
        messages={data.messages}
        address={data.address}
        loadError={error}
      />
    </div>
  );
}
