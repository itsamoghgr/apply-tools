import { getProfile } from "./actions";
import ProfileEditor from "./ProfileEditor";

export const dynamic = "force-dynamic";

// Server component: loads the singleton master profile and hands it to the
// client editor. Mirrors resume-builder/[id]/page.tsx (load → client editor).
export default async function ProfilePage() {
  const profile = await getProfile();

  return (
    <div className="space-y-6 animate-slide-up">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-semibold tracking-tight font-[family-name:var(--font-display)]">
          Profile
        </h1>
      </div>
      <p className="text-sm opacity-60 -mt-3 max-w-2xl">
        Your master career data. Resumes and outreach draw from what you keep
        here — edit it once, reuse it everywhere.
      </p>
      <ProfileEditor initialProfile={profile} />
    </div>
  );
}
