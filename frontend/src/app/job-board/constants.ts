/**
 * Shared Job Board constants.
 *
 * Deliberately NOT in actions.ts: that file carries "use server", where every
 * export must be an async function. Exporting plain arrays from it makes them
 * arrive in client components as an unusable server-reference proxy —
 * `TARGET_ROLES.map is not a function` at runtime.
 */

/** The five target roles. Mirrors DEFAULT_ROLE_MATCHERS in matcher.py. */
export const TARGET_ROLES = [
  "Data Scientist",
  "Data Analyst",
  "AI Engineer",
  "Founding Engineer",
  "Forward Deployed Engineer",
] as const;

/** Countries offered as scrape filters, most common on these boards first. */
export const COUNTRY_OPTIONS = [
  { code: "US", name: "United States" },
  { code: "CA", name: "Canada" },
  { code: "GB", name: "United Kingdom" },
  { code: "IN", name: "India" },
  { code: "DE", name: "Germany" },
  { code: "IE", name: "Ireland" },
  { code: "AU", name: "Australia" },
  { code: "SG", name: "Singapore" },
  { code: "JP", name: "Japan" },
  { code: "NL", name: "Netherlands" },
  { code: "FR", name: "France" },
  { code: "PL", name: "Poland" },
] as const;

/** Setting row holding the scan/alert schedule as JSON. Lives here rather than
 *  in actions.ts because a "use server" module may only export async
 *  functions. */
export const SCHEDULE_KEY = "jobboard.schedule";

/** The scan/alert schedule as stored in the SCHEDULE_KEY setting row. */
export type Schedule = {
  timezone: string;
  monitorIntervalH: number;
  monitorStart: string;
  monitorEnd: string;
  monitorDays: string;
  alertAt: string;
  alertDays: string;
};
