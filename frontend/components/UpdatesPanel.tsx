"use client";
// "Recently updated" panel, from GET /updates (the daily refresh's change log).
import Link from "next/link";
import { useEffect, useState } from "react";
import { updates } from "@/lib/api";
import type { UpdateRow } from "@/lib/types";
import { usePrefs } from "./Prefs";
import { formatDate } from "./SchemeCard";

export function UpdatesPanel() {
  const { lang, t } = usePrefs();
  const [rows, setRows] = useState<UpdateRow[] | null>(null);

  useEffect(() => {
    updates(5).then(setRows).catch(() => setRows([]));
  }, []);

  if (rows === null) return null;
  return (
    <section className="card">
      <h2 className="font-semibold">{t.recentlyUpdated}</h2>
      {rows.length === 0 ? (
        <p className="mt-1 text-sm text-stone-500">{t.noUpdates}</p>
      ) : (
        <ul className="mt-2 space-y-2 text-sm">
          {rows.map((r) => {
            const slugs = [...(r.details.updated ?? []), ...(r.details.new ?? [])].slice(0, 4);
            return (
              <li key={r.id} className="flex flex-col gap-0.5">
                <span>
                  <span className="text-stone-500">{formatDate(r.run_at, lang)}:</span>{" "}
                  {r.details.summary ?? `${r.updated} updated, ${r.added} new, ${r.inactive} inactive`}
                </span>
                {slugs.length > 0 && (
                  <span className="flex flex-wrap gap-1">
                    {slugs.map((s) => (
                      <Link key={s} href={`/scheme/${encodeURIComponent(s)}`} className="rounded bg-stone-100 px-1.5 py-0.5 text-xs">
                        {s}
                      </Link>
                    ))}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
