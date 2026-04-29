import Link from "next/link";
import ResumeForm from "../ResumeForm";
import { createResume } from "../actions";
import { ArrowLeft } from "lucide-react";

export default function NewResumePage() {
  return (
    <div className="space-y-6 max-w-4xl animate-slide-up">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold tracking-tight">New resume</h1>
        <Link
          href="/resumes"
          className="text-sm opacity-60 hover:opacity-100 transition-opacity flex items-center gap-1.5"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back
        </Link>
      </div>
      <div className="glass-card p-6 sm:p-8">
        <ResumeForm
          action={createResume}
          showIdField
          submitLabel="Create resume"
          initial={{ isActive: true }}
        />
      </div>
    </div>
  );
}
