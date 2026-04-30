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
  Send,
} from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { LOGO_LOCKUP_INNER } from "./logoLockupInner";

function LogoLockup({ className }: { className?: string }) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="290 118 360 124"
      role="img"
      aria-label="apply-tools"
      className={className}
      dangerouslySetInnerHTML={{ __html: LOGO_LOCKUP_INNER }}
    />
  );
}

type NavItem = {
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
};

const NAV: NavItem[] = [
  { href: "/", label: "Home", icon: Home, exact: true },
  { href: "/applications", label: "Applications", icon: Briefcase },
  { href: "/reach-out", label: "Reach Out", icon: Send },
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
  // Visual state (label visibility, width) is driven by CSS keyed to
  // `data-sidebar` on <html>, which the inline pre-paint script in layout.tsx
  // sets from localStorage before first paint. React state is just for the
  // chevron icon and click handler; it syncs in the effect below.
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    // First effect: read the persisted choice into React state so the
    // chevron icon matches the user's actual setting.
    if (!hydrated) {
      setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
      setHydrated(true);
      return;
    }
    // Subsequent state changes (user toggles): persist and update DOM attr.
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    document.documentElement.dataset.sidebar = collapsed
      ? "collapsed"
      : "expanded";
  }, [collapsed, hydrated]);

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
        <Link href="/" aria-label="apply-tools home" className="flex items-center">
          <LogoLockup className="h-9 w-auto text-base-content" />
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
        data-mobile-open={mobileOpen ? "true" : "false"}
        className={[
          "sidebar sidebar-aside group/sidebar",
          // Desktop: fixed rail on the left. Width follows --sidebar-w
          // which is set by the data-sidebar attribute on <html>.
          "hidden lg:flex flex-col fixed inset-y-0 left-0 z-30",
          "border-r border-base-300/40 bg-base-100/80 backdrop-blur",
          "transition-[width] duration-200 ease-out",
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
      <div className="sidebar-brand h-24 flex items-center justify-center px-4 border-b border-base-300/40">
        <Link
          href="/"
          aria-label="apply-tools home"
          className="flex items-center justify-center min-w-0 w-full"
        >
          <LogoLockup className="sidebar-label w-full max-w-[200px] h-auto text-base-content" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/apply-tools-logo-only.svg"
            alt="apply-tools"
            className="sidebar-icon-only h-12 w-12"
          />
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
                  title={item.label}
                  className={[
                    "sidebar-link",
                    "group flex items-center rounded-lg transition-colors",
                    "h-9 px-2.5 gap-3",
                    active
                      ? "bg-primary/15 text-primary font-medium"
                      : "opacity-75 hover:opacity-100 hover:bg-base-300/40",
                  ].join(" ")}
                >
                  <Icon className="h-[18px] w-[18px] shrink-0" />
                  <span className="sidebar-label text-sm truncate">
                    {item.label}
                  </span>
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
