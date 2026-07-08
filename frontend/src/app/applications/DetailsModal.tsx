"use client";

import { useEffect, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";

// Centered modal shell for an application's details. Mirrors the LeadPicker
// modal in this folder (portal + dimmed backdrop) but adds Esc-to-close and an
// internal scroll region so the tall details form fits any viewport. Closing is
// via backdrop click, the X button, or Escape.
export default function DetailsModal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Esc closes; lock body scroll while open so the page behind doesn't move.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-start justify-center p-4 animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      {/* Blurred/dimmed backdrop lives in its OWN layer. Putting backdrop-blur
          on the same element as the panel creates a compositing context that
          made the opaque panel render see-through in Chrome (table bled through).
          Keeping it separate lets the panel paint solidly above it. */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative mt-10 w-full max-w-3xl max-h-[85vh] flex flex-col rounded-xl bg-base-100 shadow-2xl border border-base-300/60 overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3 border-b border-base-300/40 shrink-0">
          <h2 className="text-sm font-semibold tracking-tight truncate">{title}</h2>
          <button
            onClick={onClose}
            className="text-xs opacity-60 hover:opacity-100 transition-opacity"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
