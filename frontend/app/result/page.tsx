"use client";
// Shareable result: /result?p=<base64url profile>&l=<hi|en> -> POST /eligibility -> ranked scheme list.
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { usePrefs } from "@/components/Prefs";
import { formatDate, VerificationBadge } from "@/components/SchemeCard";
import { eligibility } from "@/lib/api";
import { FIELD_LABELS } from "@/lib/i18n";
import { decodeProfile } from "@/lib/share";
import type { EligibilityResponse, Verdict } from "@/lib/types";

const ORDER: Verdict[] = ["eligible", "maybe", "no"];

function Result() {
  const params = useSearchParams();
  const { lang, setLang, t } = usePrefs();
  const [data, setData] = useState<EligibilityResponse | null>(null);
  const [error, setError] = useState(false);
  const profile = decodeProfile(params.get("p"));

  useEffect(() => {
    const l = params.get("l");
    if (l === "hi" || l === "en") setLang(l);
  }, [params, setLang]);

  useEffect(() => {
    if (!profile) return;
    eligibility(profile, lang).then(setData).catch(() => setError(true));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.get("p"), lang]);

  if (!profile) return <p className="card">{t.notFound}</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">{t.resultTitle}</h1>
      <div className="card text-sm">
        <p className="font-medium">{t.profileTitle}:</p>
        <ul className="mt-1 flex flex-wrap gap-1.5">
          {Object.entries(profile).map(([k, v]) => (
            <li key={k} className="rounded-lg bg-stone-100 px-2 py-1">
              {FIELD_LABELS[k]?.[lang] ?? k}: {String(v)}
            </li>
          ))}
        </ul>
      </div>
      {error && <p className="card text-rose-800">{t.errNetwork}</p>}
      {!data && !error && <p className="animate-pulse text-stone-500">{t.thinking}</p>}
      {data && ORDER.filter((v) => v !== "no").map((v) => {
        const items = data.schemes.filter((s) => s.verdict === v);
        if (!items.length) return null;
        return (
          <section key={v} className="space-y-2">
            <h2 className="font-semibold">{t[v]} ({data.counts[v]})</h2>
            {items.slice(0, 20).map((s) => (
              <Link key={s.slug} href={`/scheme/${encodeURIComponent(s.slug)}`} className="card block hover:border-emerald-400">
                <p className="font-medium">{s.name}</p>
                {s.reasons.length > 0 && <p className="text-sm text-stone-600">{s.reasons.join(" · ")}</p>}
                {s.missing.length > 0 && (
                  <p className="text-sm text-amber-800">{t.missingInfo}: {s.missing.map((m) => FIELD_LABELS[m]?.[lang] ?? m).join(", ")}</p>
                )}
                <div className="mt-2 flex items-center gap-2 text-xs text-stone-500">
                  <VerificationBadge value={s.verification} /> {t.lastChecked}: {formatDate(s.last_checked, lang)}
                </div>
              </Link>
            ))}
          </section>
        );
      })}
      <Link href="/" className="btn-secondary">← {t.back}</Link>
    </div>
  );
}

export default function ResultPage() {
  return <Suspense fallback={null}><Result /></Suspense>;
}
