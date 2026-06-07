"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

// Portaled modal shell. Rendering to document.body is essential here: the
// /resume-builder pages live inside <main> which has `animate-fade-in` (a
// transform), and a CSS transform creates a containing block for
// position:fixed — so a `fixed inset-0` overlay rendered in the normal tree
// gets trapped/clipped inside <main> instead of covering the viewport. The
// portal escapes that ancestor. Mirrors the pattern in applications/LeadPicker.
export default function Modal({
  title,
  children,
  onClose,
  maxWidth = "max-w-lg",
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
  maxWidth?: string;
}) {
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  if (!mounted) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm animate-fade-in"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
    >
      <div
        className={`glass-card bg-base-100 border border-base-300/60 shadow-2xl p-6 w-full ${maxWidth} space-y-4 animate-slide-up`}
      >
        {title && <h2 className="text-lg font-semibold">{title}</h2>}
        {children}
      </div>
    </div>,
    document.body,
  );
}
