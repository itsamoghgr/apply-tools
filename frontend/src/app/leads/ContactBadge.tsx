// Contactability flag for a lead. Surfaces data quality at a glance:
//   email present            → green  "Email"
//   only LinkedIn, no email   → amber  "LinkedIn only"
//   neither                   → red    "No contact"  (the thing to fix)
import { AlertTriangle, Mail, Link2 } from "lucide-react";

export type Contact = { email: string | null; linkedinUrl: string | null };

export function contactState(c: Contact): "email" | "linkedin" | "none" {
  if (c.email && c.email.trim()) return "email";
  if (c.linkedinUrl && c.linkedinUrl.trim()) return "linkedin";
  return "none";
}

const STYLE = {
  email: { color: "#3f7a5e", label: "Email", Icon: Mail },
  linkedin: { color: "#b87f3c", label: "LinkedIn only", Icon: Link2 },
  none: { color: "#c0504d", label: "No contact", Icon: AlertTriangle },
} as const;

export default function ContactBadge({ email, linkedinUrl }: Contact) {
  const state = contactState({ email, linkedinUrl });
  const { color, label, Icon } = STYLE[state];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{
        background: `color-mix(in oklab, ${color} 12%, transparent)`,
        color,
        borderColor: `color-mix(in oklab, ${color} 28%, transparent)`,
      }}
      title={
        state === "none"
          ? "No email or LinkedIn — needs a contact method"
          : state === "linkedin"
            ? "Has LinkedIn but no email — try Find emails"
            : "Has a verified email"
      }
    >
      <Icon className="h-3 w-3" />
      {label}
    </span>
  );
}
