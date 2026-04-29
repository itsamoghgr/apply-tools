"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Home,
  Briefcase,
  FileText,
  Clock,
  ChevronLeft,
  ChevronRight,
  Menu,
  X,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
};

const NAV: NavItem[] = [
  { href: "/", label: "Home", icon: Home, exact: true },
  { href: "/applications", label: "Applications", icon: Briefcase },
  { href: "/resumes", label: "Resumes", icon: FileText },
  { href: "/history", label: "History", icon: Clock },
];

const STORAGE_KEY = "sidebar:collapsed";

function isActive(pathname: string, item: NavItem): boolean {
  return item.exact
    ? pathname === item.href
    : pathname === item.href || pathname.startsWith(item.href + "/");
}

export function Sidebar() {
  const pathname = usePathname();
  // Start collapsed=true so SSR matches the inline script's pre-paint state
  // for users who chose collapsed; the effect below corrects on mount.
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    setCollapsed(stored === "1");
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!mounted) return;
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    document.documentElement.dataset.sidebar = collapsed
      ? "collapsed"
      : "expanded";
  }, [collapsed, mounted]);

  // Close mobile drawer when route changes.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <>
      {/* Mobile top bar (only visible below lg). */}
      <div className="lg:hidden sticky top-0 z-30 flex items-center justify-between px-4 h-14 border-b border-base-300/40 bg-base-100/80 backdrop-blur">
        <button
          type="button"
          onClick={() => setMobileOpen((v) => !v)}
          aria-label="Open menu"
          className="btn btn-ghost btn-sm btn-circle"
        >
          {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
        <Link
          href="/"
          className="font-semibold tracking-tight flex items-center gap-2"
        >
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-primary animate-pulse-glow" />
          <span className="gradient-text">apply-tools</span>
        </Link>
        <ThemeToggle />
      </div>

      {/* Mobile drawer backdrop. */}
      {mobileOpen && (
        <button
          type="button"
          aria-label="Close menu"
          onClick={() => setMobileOpen(false)}
          className="lg:hidden fixed inset-0 z-40 bg-black/40 backdrop-blur-sm"
        />
      )}

      <aside
        data-collapsed={collapsed ? "true" : "false"}
        data-mobile-open={mobileOpen ? "true" : "false"}
        className={[
          "sidebar group/sidebar",
          // Desktop: fixed rail on the left, width changes via data attr.
          "hidden lg:flex flex-col fixed inset-y-0 left-0 z-30",
          "border-r border-base-300/40 bg-base-100/80 backdrop-blur",
          "transition-[width] duration-200 ease-out",
          collapsed ? "w-[68px]" : "w-[224px]",
        ].join(" ")}
      >
        <SidebarContents
          pathname={pathname}
          collapsed={collapsed}
          onToggle={() => setCollapsed((v) => !v)}
        />
      </aside>

      {/* Mobile drawer (slides in from the left below lg). */}
      <aside
        className={[
          "lg:hidden fixed inset-y-0 left-0 z-50 w-[260px]",
          "border-r border-base-300/40 bg-base-100",
          "transition-transform duration-200 ease-out",
          mobileOpen ? "translate-x-0" : "-translate-x-full",
          "flex flex-col",
        ].join(" ")}
      >
        <SidebarContents
          pathname={pathname}
          collapsed={false}
          mobile
          onClose={() => setMobileOpen(false)}
        />
      </aside>
    </>
  );
}

function SidebarContents({
  pathname,
  collapsed,
  mobile,
  onToggle,
  onClose,
}: {
  pathname: string;
  collapsed: boolean;
  mobile?: boolean;
  onToggle?: () => void;
  onClose?: () => void;
}) {
  return (
    <>
      {/* Brand */}
      <div className="h-16 flex items-center px-4 border-b border-base-300/40">
        <Link
          href="/"
          className="font-semibold tracking-tight flex items-center gap-2.5 min-w-0"
        >
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-primary animate-pulse-glow shrink-0" />
          {!collapsed && (
            <span className="gradient-text truncate">apply-tools</span>
          )}
        </Link>
        {mobile && onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="btn btn-ghost btn-sm btn-circle ml-auto"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {/* Nav links */}
      <nav className="flex-1 overflow-y-auto py-3 px-2">
        <ul className="space-y-0.5">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active = isActive(pathname, item);
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  title={collapsed ? item.label : undefined}
                  className={[
                    "group flex items-center rounded-lg transition-colors",
                    "h-9 px-2.5 gap-3",
                    active
                      ? "bg-primary/15 text-primary font-medium"
                      : "opacity-75 hover:opacity-100 hover:bg-base-300/40",
                    collapsed ? "justify-center" : "",
                  ].join(" ")}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  {!collapsed && (
                    <span className="text-sm truncate">{item.label}</span>
                  )}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer: theme toggle + collapse button */}
      <div
        className={[
          "border-t border-base-300/40 px-2 py-2 flex items-center gap-1",
          collapsed ? "flex-col" : "",
        ].join(" ")}
      >
        <div className={collapsed ? "" : "ml-1"}>
          <ThemeToggle />
        </div>
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={[
              "btn btn-ghost btn-sm btn-circle transition-colors",
              collapsed ? "" : "ml-auto",
            ].join(" ")}
          >
            {collapsed ? (
              <ChevronRight className="h-4 w-4" />
            ) : (
              <ChevronLeft className="h-4 w-4" />
            )}
          </button>
        )}
      </div>
    </>
  );
}
