"use client";
import Link from "next/link";
import type { Citation, SchemeCard as Card, Verdict, Verification } from "@/lib/types";
import { FIELD_LABELS } from "@/lib/i18n";
import { usePrefs } from "./Prefs";

const VERDICT_STYLE: Record<Verdict, string> = {
  eligible: "bg-emerald-100 text-emerald-900 border-emerald-300",
  maybe: "bg-amber-100 text-amber-900 border-amber-300",
  no: "bg-stone-100 text-stone-600 border-stone-300",
};
const VERDICT_ICON: Record<Verdict, string> = { eligible: "✓", maybe: "?", no: "✕" };

export function formatDate(iso: string | null | undefined, lang: string): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(lang === "hi" ? "hi-IN" : "en-IN", { day: "numeric", month: "short", year: "numeric" });
}

export function VerificationBadge({ value }: { value: Verification | null | undefined }) {
  const { t } = usePrefs();
  if (!value) return null;
  const style = value === "verified" ? "bg-emerald-50 text-emerald-800 border-emerald-200"
    : value === "stale" ? "bg-rose-50 text-rose-800 border-rose-200" : "bg-sky-50 text-sky-800 border-sky-200";
  const label = value === "verified" ? t.verified : value === "stale" ? t.stale : t.auto;
  const help = value === "verified" ? t.verifiedHelp : value === "stale" ? t.staleHelp : t.autoHelp;
  return (
    <span title={help} className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${style}`}>
      {value === "verified" ? "✔" : value === "stale" ? "⟳" : "◎"} {label}
    </span>
  );
}

export function SchemeCard({ card }: { card: Card }) {
  const { lang, t } = usePrefs();
  const name = lang === "hi" && card.name_hi ? card.name_hi : card.name;
  const verdict = card.verdict;
  return (
    <article className="card space-y-3">
      <div className="flex flex-col items-start gap-2">
        <h3 className="text-lg font-semibold leading-snug">{name}</h3>
        {verdict && (
          <span className={`rounded-full border px-2.5 py-1 text-sm font-semibold ${VERDICT_STYLE[verdict]}`}>
            {VERDICT_ICON[verdict]} {t[verdict]}
          </span>
        )}
      </div>

      {card.benefits && <p className="text-stone-700">{card.benefits}</p>}

      {card.reasons.length > 0 && (
        <p className="text-sm text-stone-600"><span className="font-medium">{t.whyMatch}:</span> {card.reasons.join(" · ")}</p>
      )}
      {card.missing.length > 0 && (
        <p className="text-sm text-amber-800">
          <span className="font-medium">{t.missingInfo}:</span>{" "}
          {card.missing.map((m) => FIELD_LABELS[m]?.[lang] ?? m).join(", ")}
        </p>
      )}
      {card.also_check && card.also_check.length > 0 && (
        <p className="text-sm text-stone-600"><span className="font-medium">{t.alsoCheck}:</span> {card.also_check.join("; ")}</p>
      )}

      {card.documents_required.length > 0 && (
        <div>
          <p className="text-sm font-medium">{t.documents}</p>
          <ul className="mt-1 flex flex-wrap gap-1.5">
            {card.documents_required.slice(0, 6).map((d) => (
              <li key={d} className="rounded-lg bg-stone-100 px-2 py-1 text-sm">{d}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-stone-100 pt-3 text-sm">
        <VerificationBadge value={card.verification} />
        <span className="text-stone-500">{t.lastChecked}: {formatDate(card.last_checked, lang)}</span>
      </div>
      <div className="flex flex-wrap gap-2">
        {card.source_url && (
          <a href={card.source_url} target="_blank" rel="noopener noreferrer" className="btn-primary flex-1 py-2.5">
            {t.officialLink} ↗
          </a>
        )}
        <Link href={`/scheme/${encodeURIComponent(card.slug)}`} className="btn-secondary flex-1 py-2.5">
          {t.checklist}
        </Link>
      </div>
    </article>
  );
}

/** Answer text with [n] markers turned into links to the cited official page. */
export function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const byN = new Map(citations.map((c) => [c.n, c]));
  const parts = text.split(/(\[\d+\])/g);
  return (
    <p className="whitespace-pre-wrap leading-relaxed">
      {parts.map((p, i) => {
        const m = p.match(/^\[(\d+)\]$/);
        if (!m) return <span key={i}>{p}</span>;
        const c = byN.get(Number(m[1]));
        return c?.source_url ? (
          <a key={i} href={c.source_url} target="_blank" rel="noopener noreferrer" className="cite"
             title={`${c.name} — ${c.section.replace(/_/g, " ")}`}>{m[1]}</a>
        ) : <span key={i} className="cite">{m[1]}</span>;
      })}
    </p>
  );
}

export function Sources({ citations }: { citations: Citation[] }) {
  const { lang, t } = usePrefs();
  if (!citations.length) return null;
  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-stone-600">{t.sources} ({citations.length})</summary>
      <ol className="mt-2 space-y-2">
        {citations.map((c) => (
          <li key={c.n} className="rounded-lg bg-stone-50 p-2">
            <span className="cite mr-1">{c.n}</span>
            {c.source_url ? (
              <a href={c.source_url} target="_blank" rel="noopener noreferrer" className="font-medium text-emerald-800 underline">
                {c.name}
              </a>
            ) : <span className="font-medium">{c.name}</span>}
            <span className="text-stone-500"> · {c.section.replace(/_/g, " ")} · {t.lastChecked} {formatDate(c.last_checked, lang)}</span>
            <span className="ml-2"><VerificationBadge value={c.verification} /></span>
          </li>
        ))}
      </ol>
    </details>
  );
}
