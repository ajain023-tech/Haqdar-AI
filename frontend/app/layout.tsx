import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { PrefsProvider } from "@/components/Prefs";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "Haqdar AI — Hakk se, apni yojana",
  description: "Find government schemes you qualify for, in Hindi, Hinglish or English, with official links.",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icons/icon.svg", apple: "/icons/icon-192.png" },
  appleWebApp: { capable: true, title: "Haqdar AI", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#047857",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="hi">
      <body className="bg-stone-50 text-stone-900 antialiased">
        <PrefsProvider>
          <Shell>{children}</Shell>
        </PrefsProvider>
      </body>
    </html>
  );
}
