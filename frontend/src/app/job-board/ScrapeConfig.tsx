"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { X, Save, AlertTriangle } from "lucide-react";
import { updateScrapeConfig } from "./actions";
import { TARGET_ROLES, COUNTRY_OPTIONS } from "./constants";

/**
 * Per-company scrape settings.
 *
 * These are NOT display filters: they run during the scrape and postings that
 * fail them are never stored. That is why the panel says so plainly, and why
 * every filter fails OPEN on the agent side — an unparseable country or a
 * missing posted-date keeps the posting rather than dropping it.
 */
export default function ScrapeConfig({
  company,
  onClose,
}: {
  company: {
    id: string;
    name: string;
    roleFilter: string[] | null;
    countryFilter: string[] | null;
  };
  onClose: () => void;
}) {
  const router = useRouter();
  const [, startTransition] = useTransition();
  const [roles, setRoles] = useState<string[]>(company.roleFilter ?? []);
  const [countries, setCountries] = useState<string[]>(company.countryFilter ?? []);
  const [saving, setSaving] = useState(false);

  function toggle(list: string[], value: string, set: (v: string[]) => void) {
    set(list.includes(value) ? list.filter((v) => v !== value) : [...list, value]);
  }

  async function save() {
    setSaving(true);
    try {
      const res = await updateScrapeConfig(company.id, {
        roleFilter: roles,
        countryFilter: countries,
      });
      if (res.error) toast.error(res.error);
      else toast.success(res.message ?? "Saved.");
      startTransition(() => router.refresh());
      onClose();
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="mt-2 rounded-box border border-base-300 bg-base-200/40 p-4">
      <div className="mb-3 flex items-center justify-between">
        <h4 className="text-sm font-semibold">Scrape settings — {company.name}</h4>
        <button className="btn btn-ghost btn-xs btn-square" onClick={onClose}>
          <X className="h-4 w-4" />
        </button>
      </div>

      <p className="mb-4 flex items-start gap-1.5 text-xs opacity-70">
        <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        <span>
          These apply <strong>while scraping</strong> — postings that don&apos;t match
          are never stored. Leave a section empty for no restriction. A posting
          whose country can&apos;t be read is always kept. How far back to keep
          postings is a global setting, above the watchlist.
        </span>
      </p>

      <div className="space-y-4">
        <div>
          <div className="mb-1.5 text-xs font-medium opacity-70">
            Roles to watch{" "}
            <span className="opacity-60">
              {roles.length === 0 ? "(all five)" : `(${roles.length} selected)`}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {TARGET_ROLES.map((role) => {
              const on = roles.includes(role);
              return (
                <button
                  key={role}
                  onClick={() => toggle(roles, role, setRoles)}
                  className={`inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors ${
                    on ? "bg-primary text-primary-content" : "bg-base-200 hover:bg-base-300"
                  }`}
                >
                  {role}
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="mb-1.5 text-xs font-medium opacity-70">
            Countries{" "}
            <span className="opacity-60">
              {countries.length === 0 ? "(anywhere)" : `(${countries.length} selected)`}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {COUNTRY_OPTIONS.map((c) => {
              const on = countries.includes(c.code);
              return (
                <button
                  key={c.code}
                  title={c.name}
                  onClick={() => toggle(countries, c.code, setCountries)}
                  className={`inline-flex h-7 items-center rounded-md px-2.5 text-xs font-medium transition-colors ${
                    on ? "bg-primary text-primary-content" : "bg-base-200 hover:bg-base-300"
                  }`}
                >
                  {c.code}
                </button>
              );
            })}
          </div>
        </div>

      </div>

      <div className="mt-4 flex justify-end gap-2">
        <button className="btn btn-ghost btn-sm" onClick={onClose}>
          Cancel
        </button>
        <button className="btn btn-primary btn-sm gap-1.5" onClick={save} disabled={saving}>
          <Save className="h-3.5 w-3.5" />
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
