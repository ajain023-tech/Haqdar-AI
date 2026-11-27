"use client";
import { useEffect, useRef, useState } from "react";
import { AnswerText, SchemeCard, Sources } from "@/components/SchemeCard";
import { usePrefs } from "@/components/Prefs";
import { UpdatesPanel } from "@/components/UpdatesPanel";
import { ListenButton, VoiceInput } from "@/components/VoiceInput";
import { ApiError, chat } from "@/lib/api";
import { encodeProfile } from "@/lib/share";
import type { ChatFinal, Profile } from "@/lib/types";

interface Msg {
  id: number;
  role: "user" | "assistant";
  text: string;
  final?: ChatFinal;
  streaming?: boolean;
  error?: string;
}

export default function Home() {
  const { lang, t } = usePrefs();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [fromVoice, setFromVoice] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [profile, setProfile] = useState<Profile>({});
  const [copied, setCopied] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const nextId = useRef(1);

  useEffect(() => {
    const sid = sessionStorage.getItem("haqdar.session");
    if (sid) setSessionId(sid);
  }, []);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [msgs]);

  function patch(id: number, p: Partial<Msg>) {
    setMsgs((m) => m.map((x) => (x.id === id ? { ...x, ...p } : x)));
  }

  async function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setInput("");
    setFromVoice(false);
    setBusy(true);
    const uid = nextId.current++;
    const aid = nextId.current++;
    setMsgs((m) => [...m, { id: uid, role: "user", text: message }, { id: aid, role: "assistant", text: "", streaming: true }]);
    let acc = "";
    try {
      for await (const ev of chat(message, { lang: "auto", sessionId })) {
        if (ev.type === "meta") {
          setSessionId(ev.session_id);
          sessionStorage.setItem("haqdar.session", ev.session_id);
        } else if (ev.type === "token") {
          acc += ev.text;
          patch(aid, { text: acc });
        } else if (ev.type === "revise") {
          acc = ev.text;
          patch(aid, { text: acc });
        } else if (ev.type === "final") {
          setProfile(ev.profile || {});
          patch(aid, { text: ev.answer.text, final: ev, streaming: false });
        } else if (ev.type === "error") {
          patch(aid, { streaming: false, error: t.errServer });
        }
      }
    } catch (e) {
      const err = e instanceof ApiError ? (e.status === 0 ? t.errNetwork : e.status === 429 ? t.errRate : t.errServer) : t.errServer;
      patch(aid, { streaming: false, error: err });
    } finally {
      patch(aid, { streaming: false });
      setBusy(false);
    }
  }

  function newChat() {
    sessionStorage.removeItem("haqdar.session");
    setSessionId(undefined);
    setProfile({});
    setMsgs([]);
  }

  async function share() {
    const url = `${location.origin}/result?p=${encodeProfile(profile)}&l=${lang}`;
    try {
      if (navigator.share) await navigator.share({ title: "Haqdar AI", url });
      else {
        await navigator.clipboard.writeText(url);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }
    } catch { /* user cancelled */ }
  }

  const hasProfile = Object.keys(profile).length > 0;

  return (
    <div className="flex flex-col gap-4 pb-36">
      {msgs.length === 0 && (
        <section className="space-y-4">
          <p className="text-lg text-stone-700">{t.subtitle}</p>
          <div className="flex flex-col gap-2">
            {t.examples.map((ex) => (
              <button key={ex} type="button" onClick={() => send(ex)} className="btn-secondary justify-start text-left">
                “{ex}”
              </button>
            ))}
          </div>
          <UpdatesPanel />
        </section>
      )}

      {msgs.map((m) =>
        m.role === "user" ? (
          <div key={m.id} className="ml-10 self-end rounded-2xl rounded-br-sm bg-emerald-700 px-4 py-3 text-white">{m.text}</div>
        ) : (
          <div key={m.id} className="space-y-3">
            <div className="card">
              {m.error ? (
                <div className="flex items-center justify-between gap-2 text-rose-800">
                  <span>{m.error}</span>
                  <button type="button" className="btn-chip"
                    onClick={() => { const last = [...msgs].reverse().find((x) => x.role === "user"); if (last) send(last.text); }}>
                    ↻
                  </button>
                </div>
              ) : m.text ? (
                <AnswerText text={m.text} citations={m.final?.answer.citations ?? []} />
              ) : (
                <p className="animate-pulse text-stone-500">{t.thinking}</p>
              )}
              {m.final && !m.final.answer.refused && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <ListenButton text={m.final.answer.text} lang={m.final.lang} />
                </div>
              )}
              {m.final && <Sources citations={m.final.answer.citations} />}
            </div>

            {m.final?.follow_up && (
              <div className="card border-amber-300 bg-amber-50">
                <p className="font-medium">{m.final.follow_up.question}</p>
                {m.final.follow_up.options && (
                  <div className="mt-2 flex flex-wrap gap-2">
                    {m.final.follow_up.options.map((o) => (
                      <button key={o} type="button" className="btn-chip" disabled={busy} onClick={() => send(o)}>
                        {o === "yes" ? t.yes : o === "no" ? t.no_ : o}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            {m.final?.schemes.map((c) => <SchemeCard key={c.slug} card={c} />)}
          </div>
        ),
      )}
      <div ref={bottom} />

      <form
        onSubmit={(e) => { e.preventDefault(); send(input); }}
        className="fixed inset-x-0 bottom-0 z-10 mx-auto max-w-2xl border-t border-stone-200 bg-white px-4 py-3"
      >
        {fromVoice && <p className="mb-1 text-xs text-stone-600">{t.editTranscript}</p>}
        <div className="flex items-end gap-2">
          <VoiceInput disabled={busy} onTranscript={(tx) => { setInput(tx); setFromVoice(true); }} />
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
            rows={fromVoice ? 3 : 1}
            placeholder={t.placeholder}
            aria-label={t.placeholder}
            className="min-h-12 flex-1 resize-none rounded-xl border border-stone-300 px-3 py-3 focus:border-emerald-600 focus:outline-none"
          />
          <button type="submit" disabled={busy || !input.trim()} className="btn-primary h-12">{t.send}</button>
        </div>
        {msgs.length > 0 && (
          <div className="mt-2 flex gap-2">
            <button type="button" onClick={newChat} className="btn-chip">{t.newChat}</button>
            {hasProfile && (
              <button type="button" onClick={share} className="btn-chip">{copied ? t.copied : `↗ ${t.share}`}</button>
            )}
          </div>
        )}
      </form>
    </div>
  );
}
