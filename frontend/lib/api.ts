// API client for the Haqdar backend, with a contract-based mock fallback.
import { mockChat, mockEligibility, mockScheme, mockUpdates, mockVoice } from "./mock";
import type {
  ChatEvent, EligibilityResponse, Lang, Profile, SchemeDetail, UpdateRow, VoiceResult,
} from "./types";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");
export const USE_MOCK = !API_URL || process.env.NEXT_PUBLIC_USE_MOCK === "1";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, String(detail));
  }
  return res.json() as Promise<T>;
}

/** POST /chat as Server-Sent Events. Yields meta, token, revise, final, error. */
export async function* chat(message: string, opts: { lang?: Lang | "auto"; sessionId?: string; profile?: Profile } = {})
  : AsyncGenerator<ChatEvent> {
  const lang = opts.lang && opts.lang !== "auto" ? opts.lang : "auto";
  if (USE_MOCK) {
    yield* mockChat(message, lang === "auto" ? "en" : lang, opts.sessionId, opts.profile);
    return;
  }
  let res: Response;
  try {
    res = await fetch(`${API_URL}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ message, lang, session_id: opts.sessionId, profile: opts.profile, stream: true }),
    });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok || !res.body) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, String(detail));
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("");
      if (data) {
        try { yield JSON.parse(data) as ChatEvent; } catch { /* partial or malformed event: skip */ }
      }
    }
  }
}

export function eligibility(profile: Profile, lang: Lang = "en"): Promise<EligibilityResponse> {
  if (USE_MOCK) return mockEligibility(profile);
  return json("/eligibility", { method: "POST", body: JSON.stringify({ profile, lang }) });
}

export async function scheme(slug: string): Promise<SchemeDetail | null> {
  if (USE_MOCK) return mockScheme(slug);
  try {
    return await json<SchemeDetail>(`/schemes/${encodeURIComponent(slug)}`);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export async function updates(limit = 10): Promise<UpdateRow[]> {
  if (USE_MOCK) return mockUpdates();
  return (await json<{ updates: UpdateRow[] }>(`/updates?limit=${limit}`)).updates;
}

/** Step 1 of voice: audio -> transcript only, so the user can fix it before sending. */
export async function transcribe(audio: Blob, lang?: Lang): Promise<VoiceResult> {
  if (USE_MOCK) return mockVoice();
  const form = new FormData();
  form.append("audio", audio, "speech.webm");
  form.append("transcribe_only", "true");
  if (lang) form.append("lang", lang);
  let res: Response;
  try {
    res = await fetch(`${API_URL}/voice`, { method: "POST", body: form });
  } catch {
    throw new ApiError(0, "network");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not JSON */ }
    throw new ApiError(res.status, String(detail));
  }
  return res.json();
}

/** Text-to-speech for an answer: server voice (Sarvam) when configured, else null (browser speech is used). */
export async function speak(text: string, lang: Lang): Promise<string | null> {
  if (USE_MOCK) return null;
  const form = new FormData();
  form.append("audio", new Blob([], { type: "audio/webm" }), "empty.webm");
  form.append("transcript", text);
  form.append("lang", lang);
  form.append("speak_only", "true");
  try {
    const res = await fetch(`${API_URL}/voice`, { method: "POST", body: form });
    if (!res.ok) return null;
    return (await res.json()).audio_url ?? null;
  } catch {
    return null;
  }
}
