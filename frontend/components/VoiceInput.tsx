"use client";
// Voice in: record with MediaRecorder -> /voice (transcribe only) -> editable transcript -> send.
// Voice out: server TTS (Sarvam) when configured, otherwise the browser's speech synthesis.
import { useRef, useState } from "react";
import { speak as serverSpeak, transcribe } from "@/lib/api";
import type { Lang } from "@/lib/types";
import { usePrefs } from "./Prefs";

export function VoiceInput({ onTranscript, disabled }: { onTranscript: (text: string) => void; disabled?: boolean }) {
  const { lang, t } = usePrefs();
  const [state, setState] = useState<"idle" | "recording" | "transcribing">("idle");
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);

  async function start() {
    setError(null);
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError(t.errMic);
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(stream);
      chunks.current = [];
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((tr) => tr.stop());
        setState("transcribing");
        try {
          const res = await transcribe(new Blob(chunks.current, { type: r.mimeType || "audio/webm" }), lang);
          onTranscript(res.transcript);
        } catch {
          setError(t.errVoice);
        } finally {
          setState("idle");
        }
      };
      rec.current = r;
      r.start();
      setState("recording");
    } catch {
      setError(t.errMic);
    }
  }

  function stop() {
    rec.current?.stop();
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        disabled={disabled || state === "transcribing"}
        onClick={state === "recording" ? stop : start}
        className={`btn-secondary h-12 min-w-12 ${state === "recording" ? "animate-pulse border-rose-400 bg-rose-50 text-rose-800" : ""}`}
        aria-label={state === "recording" ? t.stop : t.speak}
      >
        {state === "recording" ? "■" : "🎤"}
        <span className="hidden sm:inline">{state === "recording" ? t.stop : t.speak}</span>
      </button>
      {state === "recording" && <span className="text-xs text-rose-700">{t.listening}</span>}
      {state === "transcribing" && <span className="text-xs text-stone-500">{t.transcribing}</span>}
      {error && <span className="text-xs text-rose-700">{error}</span>}
    </div>
  );
}

export function ListenButton({ text, lang }: { text: string; lang: Lang }) {
  const { t } = usePrefs();
  const [busy, setBusy] = useState(false);

  async function play() {
    setBusy(true);
    try {
      const url = await serverSpeak(text.replace(/\[\d+\]/g, ""), lang);
      if (url) {
        await new Audio(url).play();
        return;
      }
      if ("speechSynthesis" in window) {
        const u = new SpeechSynthesisUtterance(text.replace(/\[\d+\]/g, ""));
        u.lang = lang === "en" ? "en-IN" : "hi-IN";
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(u);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <button type="button" onClick={play} disabled={busy} className="btn-chip">
      🔊 {t.listen}
    </button>
  );
}
