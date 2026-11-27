# Haqdar AI — frontend

Next.js 15 + Tailwind 4, installable as an app. Mobile-first, Hindi/English, large-text mode.

```bash
cd frontend
npm install
cp .env.example .env.local     # set NEXT_PUBLIC_API_URL, or leave empty for the mock API
npm run dev                    # http://localhost:3000
npm run build && npm run typecheck
```

With `NEXT_PUBLIC_API_URL` empty (or `NEXT_PUBLIC_USE_MOCK=1`) the app runs on a mock built from the frozen API
contract ([lib/mock.ts](lib/mock.ts)), so UI work never waits for the backend.

| Screen | File |
| --- | --- |
| Chat: streamed answers with [n] citations, scheme cards, follow-up quick replies, voice in/out, share | [app/page.tsx](app/page.tsx) |
| Scheme detail + document checklist (saved on the device, works offline) | [app/scheme/[slug]/page.tsx](app/scheme/[slug]/page.tsx) |
| Shareable result: `/result?p=<profile>` (eligibility fields only, no names or IDs) | [app/result/page.tsx](app/result/page.tsx) |
| Recently updated panel (`GET /updates`) | [components/UpdatesPanel.tsx](components/UpdatesPanel.tsx) |
| Scheme card: verdict, reasons, missing info, documents, official link, last checked, verification badge | [components/SchemeCard.tsx](components/SchemeCard.tsx) |
| Voice: record → `/voice` transcribe-only → editable transcript → send; answers read aloud (server TTS, else browser speech) | [components/VoiceInput.tsx](components/VoiceInput.tsx) |

Installable app: [public/manifest.webmanifest](public/manifest.webmanifest), icons, and a service worker
([public/sw.js](public/sw.js)) that caches the app shell and shows [public/offline.html](public/offline.html)
when there's no network. API calls are never cached.

**Deploy (Vercel):** import the repo, root directory `frontend`, set `NEXT_PUBLIC_API_URL` to the Render URL, and
add the Vercel URL to the backend's `CORS_ORIGINS`.
