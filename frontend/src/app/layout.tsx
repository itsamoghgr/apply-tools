import type { Metadata } from "next";
import { Inter } from "next/font/google";
import { Geist_Mono } from "next/font/google";
import { Fraunces } from "next/font/google";
import { Toaster } from "sonner";
import "./globals.css";
import { Sidebar } from "../components/Sidebar";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

const fraunces = Fraunces({
  variable: "--font-fraunces",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Apply Tools",
  description: "Resume manager + cover letter / email / outreach generator",
};

// Inline pre-paint scripts so the theme + sidebar width don't flicker.
const initScript = `
(function(){try{
  var t=localStorage.getItem('theme');
  var d=window.matchMedia('(prefers-color-scheme: dark)').matches;
  var isDark = t==='dark' || (!t && d);
  document.documentElement.dataset.theme = isDark ? 'applydark' : 'applylight';
  var s=localStorage.getItem('sidebar:collapsed');
  document.documentElement.dataset.sidebar = s==='1' ? 'collapsed' : 'expanded';
}catch(e){}})();
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${geistMono.variable} ${fraunces.variable} h-full antialiased`}
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: initScript }} />
      </head>
      <body className="min-h-full bg-base-100 text-base-content">
        <Sidebar />

        {/* Main column shifts right of the desktop sidebar; full-width on mobile.
            pt-14 reserves space for the mobile sticky top bar (h-14). */}
        <div className="min-h-screen flex flex-col pt-14 lg:pt-0 lg:pl-[var(--sidebar-w,224px)] transition-[padding-left] duration-200 ease-out">
          <main className="flex-1 w-full px-6 sm:px-8 py-8 sm:py-10 animate-fade-in">
            {children}
          </main>

          <footer className="w-full border-t border-base-300/40 py-5">
            <div className="px-6 sm:px-8 flex items-center justify-between text-xs opacity-40">
              <span>Apply Tools · localhost</span>
              <span>v0.1.0</span>
            </div>
          </footer>
        </div>

        <Toaster
          position="top-right"
          theme="system"
          richColors
          expand={true}
          gap={12}
          visibleToasts={5}
          duration={4000}
        />
      </body>
    </html>
  );
}
