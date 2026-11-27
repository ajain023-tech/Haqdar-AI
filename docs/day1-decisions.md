# Day 1 decisions and open human tasks

## Decisions (defaults taken; change here if you disagree)

| Decision | Chosen | Why |
| --- | --- | --- |
| Embeddings | Gemini `gemini-embedding-001` @ 768 dims | Nothing to host; multilingual. bge-m3 on a Space stays the fallback if Hindi recall is weak in the live eval |
| Channel | Web app only (installable) | WhatsApp is a stretch goal |
| State | Uttar Pradesh | `HAQDAR_STATE` in the refresh job; the loader's `--state` |
| Data | Hugging Face dump on Day 1, own myScheme scraper from Day 2 | Both are in the repo (`scripts/load_hf_dataset.py`, `ingest/`) |
| Hindi content | Search Hindi questions against English official text via query rewriting | myScheme's Hindi mode translates the interface but serves English scheme content for the schemes checked |

## Tasks only a person can do

- [ ] **Apply for API Setu access** to the myScheme service API (apisetu.gov.in → "myScheme"). Don't depend on it this week.
- [ ] **Check free-tier limits** on the day and note them here: Gemini (requests/min, tokens/day), Sarvam or Bhashini credits, Supabase/Neon storage, Upstash commands/day, Render hours.
- [ ] **Hand-verify the top 50 schemes:** `python -m ingest.verify apply-drafts`, then `python -m ingest.verify export --top 50`, check each row against the guideline PDF, mark `verified = y` (fix `corrected_criteria` if needed), then `python -m ingest.verify import verification/checklist.csv`.
- [ ] **Record the 2-minute demo video** following `docs/demo-script.md`.
- [ ] **Make the pitch deck** from `docs/pitch-deck.md` once the live numbers are in.

## Scraper note

myScheme throttles fast crawls: after about 30 pages in a row at one per second it answers "Something went
wrong". The scraper now waits 3 s between pages, backs off 60 s once on an error, stops after 5 errors in a row,
and keeps every page already fetched, so the daily job simply continues the next day. A full first crawl of
~650 central + UP schemes therefore takes a few nights; until then the Hugging Face data fills the gaps.
