"use client";

import { useEffect, useState } from "react";
import { Sun, Moon } from "lucide-react";

type Pref = "light" | "dark";

const STORAGE_KEY = "theme";
const LIGHT = "applylight";
const DARK = "applydark";

function readStored(): Pref {
  if (typeof window === "undefined") return "dark";
  const v = window.localStorage.getItem(STORAGE_KEY);
  if (v === "light" || v === "dark") return v;
  // Default based on system preference
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(pref: Pref) {
  document.documentElement.dataset.theme = pref === "dark" ? DARK : LIGHT;
}

export function ThemeToggle() {
  const [pref, setPref] = useState<Pref>("dark");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPref(readStored());
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    applyTheme(pref);
    window.localStorage.setItem(STORAGE_KEY, pref);
  }, [pref, mounted]);

  const toggle = () => {
    const next: Pref = pref === "light" ? "dark" : "light";
    applyTheme(next);
    window.localStorage.setItem(STORAGE_KEY, next);
    setPref(next);
  };

  const label = pref === "light" ? "Light theme" : "Dark theme";

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Theme: ${label}. Click to change.`}
      title={label}
      className="btn btn-ghost btn-circle btn-sm transition-transform duration-300 hover:rotate-12"
    >
      {!mounted ? (
        <Moon className="h-4 w-4 transition-all duration-300" />
      ) : pref === "light" ? (
        <Sun className="h-4 w-4 transition-all duration-300" />
      ) : (
        <Moon className="h-4 w-4 transition-all duration-300" />
      )}
    </button>
  );
}
