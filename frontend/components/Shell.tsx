"use client";
// Header (language + text-size toggles), offline banner, footer with disclaimer and data credit.
import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { USE_MOCK } from "@/lib/api";
import { usePrefs } from "./Prefs";

export function Shell({ children }: { children: ReactNode }) {
  const { lang, setLang, large, setLarge, t } = usePrefs();
  const [online, setOnline] = useState(true);

  useEffect(() => {
    const update = () => setOnline(navigator.onLine);
    update();
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    if ("serviceWorker" in navigator && process.env.NODE_ENV === "production") {
      navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    }
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);

  return (
    <div className="mx-auto flex min-h-dvh max-w-2xl flex-col">
      <header className="sticky top-0 z-10 flex items-center justify-between gap-2 border-b border-stone-200 bg-white/95 px-4 py-3 backdrop-blur">
        <Link href="/" className="flex items-center gap-2">
          <img src="/icons/icon.svg" alt="" width={32} height={32} className="rounded-lg" />
          <span className="leading-tight">
            <span className="block text-lg font-bold text-emerald-800">Haqdar AI</span>
            <span className="block text-xs text-stone-500">{t.tagline}</span>
          </span>
        </Link>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setLarge(!large)}
            aria-pressed={large}
            className={`btn-chip ${large ? "bg-emerald-700 text-white" : ""}`}
            title={t.largeText}
          >
            A<span className="text-lg">A</span>
          </button>
          <div className="flex overflow-hidden rounded-full border border-stone-300" role="group" aria-label="Language">
            {(["hi", "en"] as const).map((l) => (
              <button
                key={l}
                type="button"
                onClick={() => setLang(l)}
                aria-pressed={lang === l}
                className={`px-3 py-1.5 text-sm font-medium ${lang === l ? "bg-emerald-700 text-white" : "bg-white text-stone-700"}`}
              >
                {l === "hi" ? "हिंदी" : "English"}
              </button>
            ))}
          </div>
        </div>
      </header>

      {!online && <div role="status" className="bg-amber-100 px-4 py-2 text-sm text-amber-900">{t.offline}</div>}
      {USE_MOCK && <div className="bg-sky-50 px-4 py-1.5 text-xs text-sky-800">{t.mock}</div>}

      <main className="flex-1 px-4 py-4">{children}</main>

      <footer className="border-t border-stone-200 px-4 py-4 text-xs text-stone-500">
        <p>{t.disclaimer}</p>
        <p className="mt-1">{t.credit}</p>
      </footer>
    </div>
  );
}
