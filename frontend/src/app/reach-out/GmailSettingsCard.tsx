"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Mail, CheckCircle2, AlertCircle, ExternalLink } from "lucide-react";

type Props = {
  initial: {
    address: string | null;
    fromName: string | null;
    hasPassword: boolean;
  };
};

export default function GmailSettingsCard({ initial }: Props) {
  const router = useRouter();
  const [isPending, startTransition] = useTransition();
  const [editing, setEditing] = useState(
    !initial.address || !initial.hasPassword,
  );
  const [address, setAddress] = useState(initial.address ?? "");
  const [fromName, setFromName] = useState(initial.fromName ?? "");
  const [appPassword, setAppPassword] = useState("");

  const connected = Boolean(initial.address && initial.hasPassword);

  function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!address.trim() || !address.includes("@")) {
      toast.error("Enter a valid Gmail address.");
      return;
    }
    if (!initial.hasPassword && !appPassword.trim()) {
      toast.error("App password is required to connect.");
      return;
    }
    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/settings/gmail", {
          method: "PUT",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            address: address.trim(),
            appPassword: appPassword.trim(),
            fromName: fromName.trim(),
          }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail ?? `Save failed (${res.status})`);
        }
        toast.success("Gmail connected.");
        setAppPassword("");
        setEditing(false);
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  function handleDisconnect() {
    if (!confirm("Disconnect Gmail? You'll need to re-enter the app password.")) {
      return;
    }
    startTransition(async () => {
      try {
        const res = await fetch("/api/proxy/settings/gmail", { method: "DELETE" });
        if (!res.ok) throw new Error(`Disconnect failed (${res.status})`);
        toast.success("Gmail disconnected.");
        setAddress("");
        setFromName("");
        setAppPassword("");
        setEditing(true);
        router.refresh();
      } catch (err) {
        toast.error((err as Error).message);
      }
    });
  }

  if (connected && !editing) {
    return (
      <div className="glass-card p-5 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <div className="p-2 rounded-lg bg-success/10 text-success">
            <CheckCircle2 className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-medium truncate">
              Connected as {initial.address}
              {initial.fromName ? (
                <span className="opacity-60"> · {initial.fromName}</span>
              ) : null}
            </div>
            <div className="text-xs opacity-60">
              Emails will be sent from this account.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
          >
            Edit
          </button>
          <button
            type="button"
            onClick={handleDisconnect}
            className="btn btn-ghost btn-sm text-error"
            disabled={isPending}
          >
            Disconnect
          </button>
        </div>
      </div>
    );
  }

  return (
    <form onSubmit={handleSave} className="glass-card p-5 space-y-4">
      <div className="flex items-center gap-3">
        <div className="p-2 rounded-lg bg-primary/10 text-primary">
          <Mail className="h-5 w-5" />
        </div>
        <div>
          <h2 className="text-base font-semibold">Connect Gmail</h2>
          <p className="text-xs opacity-60">
            Mail is sent over SMTP using a Gmail app password.
          </p>
        </div>
      </div>

      <div className="rounded-lg border border-warning/30 bg-warning/5 p-3 text-xs flex gap-2">
        <AlertCircle className="h-4 w-4 shrink-0 mt-0.5 text-warning" />
        <div className="space-y-1">
          <div>
            App passwords require 2-Step Verification on your Google account.
          </div>
          <a
            href="https://myaccount.google.com/apppasswords"
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            Generate one at myaccount.google.com/apppasswords
            <ExternalLink className="h-3 w-3" />
          </a>
        </div>
      </div>

      <div className="grid sm:grid-cols-2 gap-3">
        <div>
          <label className="label" htmlFor="gmail-address">
            <span className="label-text uppercase tracking-widest text-xs opacity-50 font-medium">
              Gmail address
            </span>
          </label>
          <input
            id="gmail-address"
            type="email"
            className="input input-bordered w-full"
            placeholder="you@gmail.com"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            required
          />
        </div>
        <div>
          <label className="label" htmlFor="gmail-from-name">
            <span className="label-text uppercase tracking-widest text-xs opacity-50 font-medium">
              From name (optional)
            </span>
          </label>
          <input
            id="gmail-from-name"
            type="text"
            className="input input-bordered w-full"
            placeholder="Amogh Ramagiri"
            value={fromName}
            onChange={(e) => setFromName(e.target.value)}
          />
        </div>
      </div>

      <div>
        <label className="label" htmlFor="gmail-app-password">
          <span className="label-text uppercase tracking-widest text-xs opacity-50 font-medium">
            App password{" "}
            {initial.hasPassword ? (
              <span className="opacity-60 normal-case tracking-normal">
                (leave blank to keep current)
              </span>
            ) : null}
          </span>
        </label>
        <input
          id="gmail-app-password"
          type="password"
          className="input input-bordered w-full font-mono"
          placeholder="xxxx xxxx xxxx xxxx"
          value={appPassword}
          onChange={(e) => setAppPassword(e.target.value)}
          autoComplete="new-password"
        />
      </div>

      <div className="flex items-center gap-2">
        <button
          type="submit"
          disabled={isPending}
          className="btn btn-gradient btn-sm"
        >
          {isPending ? (
            <span className="loading loading-spinner loading-xs" />
          ) : null}
          {connected ? "Save changes" : "Connect Gmail"}
        </button>
        {connected && (
          <button
            type="button"
            onClick={() => {
              setEditing(false);
              setAppPassword("");
            }}
            className="btn btn-ghost btn-sm"
            disabled={isPending}
          >
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
