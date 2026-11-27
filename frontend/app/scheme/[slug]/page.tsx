"use client";
// Scheme detail + document checklist. Ticks are stored on the device so they work offline.
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { usePrefs } from "@/components/Prefs";
import { formatDate, VerificationBadge } from "@/components/SchemeCard";
import { scheme as fetchScheme } from "@/lib/api";
import type { SchemeDetail } from "@/lib/types";

export default function SchemePage() {
  const { slug } = useParams<{ slug: string }>();
  const { lang, t } = usePrefs();
  const [s, setS] = useState<SchemeDetail | null | undefined>(undefined);
  const [error, setError] = useState(false);
  const [done, setDone] = useState<Record<string, boolean>>({});
  const key = `haqdar.checklist.${slug}`;

  useEffect(() => {
    try { setDone(JSON.parse(localStorage.getItem(key) || "{}")); } catch { setDone({}); }
    // Cache the last good copy so the checklist opens offline.
    fetchScheme(slug)
      .then((d) => { setS(d); if (d) localStorage.setItem(`${key}.scheme`, JSON.stringify(d)); })
      .catch(() => {
        const cached = localStorage.getItem(`${key}.scheme`);
        if (cached) setS(JSON.parse(cached));
        else setError(true);
      });
  }, [slug, key]);

  function toggle(doc: string) {
    const next = { ...done, [doc]: !done[doc] };
    setDone(next);
    localStorage.setItem(key, JSON.stringify(next));
  }

  if (error) return <p className="card text-rose-800">{t.errNetwork}</p>;
  if (s === undefined) return <p className="animate-pulse text-stone-500">{t.thinking}</p>;
  if (s === null) return <p className="card">{t.notFound}</p>;

  const docs = s.documents_required || [];
  const ticked = docs.filter((d) => done[d]).length;
  const name = lang === "hi" && s.name_hi ? s.name_hi : s.name;

  return (
    <div className="space-y-4">
      <Link href="/" className="text-sm text-emerald-800">← {t.back}</Link>
      <header className="space-y-2">
        <h1 className="text-2xl font-bold leading-tight">{name}</h1>
        {s.ministry && <p className="text-stone-600">{s.ministry}</p>}
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <VerificationBadge value={s.verification} />
          <span className="text-stone-500">{t.lastChecked}: {formatDate(s.last_checked, lang)}</span>
        </div>
      </header>

      {s.benefits && <p className="card text-stone-800">{s.benefits}</p>}

      <section className="card">
        <div className="flex items-baseline justify-between">
          <h2 className="font-semibold">{t.checklist}</h2>
          {docs.length > 0 && <span className="text-sm text-stone-500">{ticked}/{docs.length}</span>}
        </div>
        <p className="mt-1 text-sm text-stone-500">{t.checklistHelp}</p>
        <ul className="mt-3 space-y-2">
          {docs.map((d) => (
            <li key={d}>
              <label className="flex cursor-pointer items-center gap-3 rounded-xl border border-stone-200 p-3 has-[:checked]:border-emerald-400 has-[:checked]:bg-emerald-50">
                <input type="checkbox" checked={!!done[d]} onChange={() => toggle(d)} className="h-6 w-6 accent-emerald-700" />
                <span className={done[d] ? "text-stone-500 line-through" : ""}>{d}</span>
              </label>
            </li>
          ))}
        </ul>
      </section>

      <div className="flex flex-col gap-2">
        {s.source_url && (
          <a href={s.source_url} target="_blank" rel="noopener noreferrer" className="btn-primary">{t.officialLink} ↗</a>
        )}
        {s.pdf_url && (
          <a href={s.pdf_url} target="_blank" rel="noopener noreferrer" className="btn-secondary">Guidelines (PDF) ↗</a>
        )}
      </div>
      <p className="text-xs text-stone-500">{s.data_credit ?? t.credit}</p>
    </div>
  );
}
