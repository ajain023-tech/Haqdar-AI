# Haqdar AI — *Hakk se, apni yojana*

A free, voice-first assistant that tells a citizen which government schemes they qualify for, why, and which
documents to bring, in Hindi, Hinglish or English, with an official myScheme / .gov.in link on every answer.

**What makes it different:** eligibility is decided by **deterministic rules** over structured criteria. The LLM
only explains the result and must cite a retrieved chunk for every claim, so it cannot make up eligibility.

> Unofficial assistant. Final eligibility is decided by the department; apply via the official link.
> **Data: myScheme.gov.in, Govt. of India.**

---

## Architecture

```mermaid
flowchart LR
    subgraph Ingestion["Offline ingestion (daily)"]
        HF[HF myScheme dump / myScheme scraper] --> P[Parse sections<br/>EN + HI]
        P --> H{content_hash<br/>changed?}
        H -- no --> SKIP[skip: no re-embed]
        H -- yes --> C[Section-aware chunking<br/>~500 tokens, overlap]
        C --> E[Embeddings]
        E --> DB[(Postgres<br/>pgvector HNSW + tsvector GIN<br/>schemes / chunks / versions)]
    end

    subgraph Query["Per question"]
        U[User text / voice] --> LANG[Detect language]
        LANG --> PROF[Profile extractor<br/>eligibility fields only]
        PROF --> R{Router}
        R -- find schemes --> RULES[[Rule engine<br/>eligible / maybe / no]]
        RULES --> FU[Follow-up question<br/>for the most useful missing field]
        R -- scheme X --> RES[Resolve scheme name<br/>aliases, HI/Hinglish]
        RULES --> RET
        RES --> RET[Hybrid retrieval<br/>vector + BM25, RRF<br/>+ English rewrite of HI queries]
        RET --> RR[Reranker<br/>bge-reranker-v2-m3]
        RR --> IDK{score ≥ threshold?}
        IDK -- no --> REF["I couldn't find this in official sources"]
        IDK -- yes --> GEN[Answer generator<br/>cited, user's language]
        GEN --> SC{Self-check<br/>claim → chunk}
        SC -- fail --> RET2[Re-retrieve once<br/>then drop unsupported claims]
        SC -- ok --> OUT[Streamed answer + citations<br/>+ scheme cards + documents]
    end
    DB --> RULES
    DB --> RET
    REDIS[(Redis<br/>answer cache + rate limit)] -.-> GEN
```

| Layer | Pick (free tier) | Where |
| --- | --- | --- |
| API | FastAPI on Render free | [backend/api/main.py](backend/api/main.py), [render.yaml](render.yaml) |
| Database | Postgres + pgvector + full-text (Supabase / Neon) | [db/schema.sql](db/schema.sql), [backend/rag/store.py](backend/rag/store.py) |
| Embeddings | Gemini `gemini-embedding-001` @ 768 dims | [backend/providers/embeddings.py](backend/providers/embeddings.py) |
| Reranker | bge-reranker-v2-m3 on a free HF Space; Gemini, then lexical fallback | [reranker_space/](reranker_space/), [backend/providers/reranker.py](backend/providers/reranker.py) |
| LLM | Gemini Flash behind a provider interface | [backend/providers/llm.py](backend/providers/llm.py) |
| Agent | Plain Python state machine | [backend/agent/flow.py](backend/agent/flow.py) |
| Cache + rate limit | Upstash Redis (in-memory fallback) | [backend/core/cache.py](backend/core/cache.py), [backend/core/ratelimit.py](backend/core/ratelimit.py) |
| Tracing | Langfuse (ingestion API, no SDK) | [backend/core/tracing.py](backend/core/tracing.py) |
| Speech | Sarvam AI (voice UX is Person B's lane) | [backend/providers/speech.py](backend/providers/speech.py) |
| CI + keep-warm | GitHub Actions | [.github/workflows/](.github/workflows/) |

Every provider sits behind one interface, so Gemini can be swapped for an open model in config.
With no `GEMINI_API_KEY` the whole pipeline runs **offline** (hash embeddings, lexical reranker, extractive
answers): that is what the tests and CI use.

---

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # add GEMINI_API_KEY for real answers

# Option A: full local stack (Postgres + pgvector, Redis, API)
docker compose up --build -d
docker compose exec api python -m scripts.load_hf_dataset --state "Uttar Pradesh" --limit 250

# Option B: no Docker, in-memory store (dev only; data is lost on restart)
uvicorn backend.api.main:app --reload

pytest -q                                                            # 41 tests (+4 Postgres tests, see below)
python -m eval.run_eval --corpus eval/corpus_sample.jsonl            # metrics table
```

Postgres integration tests run when `TEST_DATABASE_URL` points at a pgvector database (CI starts one):

```bash
docker run -d -p 55432:5432 -e POSTGRES_USER=haqdar -e POSTGRES_PASSWORD=haqdar -e POSTGRES_DB=haqdar pgvector/pgvector:pg16
TEST_DATABASE_URL=postgresql://haqdar:haqdar@localhost:55432/haqdar pytest tests/test_pgstore.py
```

### Loading data

`scripts/load_hf_dataset.py` loads the Hugging Face dump `shrijayan/gov_myscheme` (2,153 myScheme pages as PDFs;
2,135 parse cleanly). It keeps central schemes plus one state (UP by default), parses each page into
*details / benefits / eligibility / exclusions / application process / documents / FAQ*, extracts a document list
and bootstrap criteria, then chunks and embeds. Re-running it only re-embeds schemes whose `content_hash` changed;
old copies go to `scheme_versions`.

```bash
python -m scripts.load_hf_dataset --state "Uttar Pradesh" --limit 250 --dump data/hf_up.jsonl
python -m scripts.load_hf_dataset --from-jsonl data/hf_up.jsonl     # re-index a parsed dump
```

**Own scraper (Person B).** [ingest/](ingest/) loads public myScheme scheme pages in headless Chromium, keeps
the JSON each page fetches while rendering (details, documents, FAQs) plus the rendered text, and caches the raw
copy with `source_url` and fetch time. It parses fields, extracts criteria (Gemini; regex offline) and indexes
through the same `index_scheme`. myScheme throttles fast crawls, so it goes one page every 3 s, backs off, and stops
after 5 errors in a row, keeping what it has (see [docs/day1-decisions.md](docs/day1-decisions.md)).

```bash
pip install -r ingest/requirements.txt && python -m playwright install chromium
python -m ingest.scraper --discover --limit 250   # sitemap + seed list -> data/raw/myscheme/
python -m ingest.refresh                          # daily job: diff, re-extract changed, inactive/stale, change log
python -m ingest.refresh --offline                # re-process cached pages only
python -m ingest.verify apply-drafts              # load hand-drafted criteria (ingest/criteria_drafts.json)
python -m ingest.verify export --top 50           # checklist for hand verification against guideline PDFs
python -m ingest.verify import verification/checklist.csv
python -m ingest.verify cross-check --top 50      # flag page vs guideline-PDF disagreements (needs Gemini)
```

The refresh runs nightly in [daily-refresh.yml](.github/workflows/daily-refresh.yml) (needs the `DATABASE_URL`
and `GEMINI_API_KEY` secrets). Scheme status: **verified** = checked by hand against the guideline PDF;
**auto** = extracted, not reviewed; **stale** = not re-checked for 30+ days, or changed after verification.

`eval/corpus_sample.jsonl` is a committed 215-scheme sample (the 20 gold schemes, 150 other central schemes,
45 UP schemes) so CI and the eval run without network access.

---

## API contract (frozen Day 1)

| Method | Path | Returns |
| --- | --- | --- |
| POST | `/chat` | SSE stream (or JSON with `"stream": false`): answer, citations, scheme cards, follow-up question |
| POST | `/voice` | multipart `audio` (+ optional edited `transcript`) → transcript + same as `/chat` + `audio_url`; `transcribe_only=true` → transcript only (for editing), `speak_only=true` → `audio_url` for `transcript` |
| POST | `/eligibility` | profile → ranked schemes with eligible / maybe / no + reasons + follow-up |
| POST | `/search` | retrieval only: `{query, state?, category?, mode: vector\|hybrid\|rerank, k}` |
| GET | `/schemes/{slug}` | scheme detail, documents, source, last checked, verification |
| GET | `/updates` | recent change log ("3 updated, 1 new") |
| POST | `/admin/ingest` | trigger a load / re-crawl (`X-Admin-Token`); `GET` shows the last run |
| GET | `/health` | liveness + DB check, used by the keep-warm ping |
| GET | `/metrics` | LLM / embedding usage counters and estimated cost per 1,000 queries |

**`POST /chat`** request: `{"message": "...", "session_id": "<uuid>?", "lang": "auto|en|hi|hinglish", "profile": {...}?, "stream": true}`

SSE events, in order:

| Event | Payload |
| --- | --- |
| `meta` | `session_id`, `lang`, `intent` (`find_schemes` / `scheme_question`), `profile`, `trace_id` |
| `token` | `text`: the next piece of the answer as the LLM writes it |
| `revise` | `text`: the self-check changed the answer, replace what was streamed |
| `final` | `answer {text, lang, citations[], refused, disclaimer}`, `schemes[]` (cards), `follow_up {field, question, affects, options?}`, `self_check`, `retrieval`, `timings_ms`, `cached` |
| `error` | `detail` |

Scheme card: `slug, name, state, category, verdict, reasons[], missing[], also_check[], benefits, documents_required[], source_url, last_checked, verification`.

Citation: `n, slug, name, section, source_url, last_checked, verification, snippet`.

Profile fields (the only personal data accepted, stored or sent to the LLM): `state, age, gender, occupation,
annual_income, land_acres, social_category, disability, bpl`. Names, phone numbers and Aadhaar are dropped.

---

## Evaluation

The same 100 questions (40 Hindi, 30 Hinglish, 30 English; about a third describe a need without naming the
scheme) plus 10 trick questions about schemes that don't exist run through four setups:
[eval/build_questions.py](eval/build_questions.py), [eval/run_eval.py](eval/run_eval.py).

**Offline run** (hash embeddings, lexical reranker, extractive answers; 215-scheme corpus with the drafted criteria
for the 18 eval schemes; 27 Nov 2026):

| Setup | Recall@5 | Faithfulness | Eligibility accuracy | Refusal (trick) | False refusals | p95 latency (s) |
| --- | --- | --- | --- | --- | --- | --- |
| Vector only | 85% | — | — | 10/10 | 0 | 0.08 |
| + BM25 hybrid | 95% | — | — | 10/10 | 0 | 0.09 |
| + Reranker | 96% | — | — | 10/10 | 0 | 0.09 |
| + Rule engine + self-check | 95% | — | 98% (60/61) | 10/10 | 0 | 0.09 |

Read these as a pipeline regression check, not the headline numbers: offline answers are extractive, so
faithfulness is trivially 100% and is left out, and latency excludes every network call. Recall by language for
the last row: Hindi 93%, Hinglish 97%, English 97%.

Eligibility accuracy compares rule-engine verdicts with hand labels for 10 personas × their candidate schemes
([eval/profiles.json](eval/profiles.json), labels taken from each scheme's official rules). With only the regex
bootstrap criteria it is 46%; with the drafted criteria it is 98%. The one miss is a real limit: the widow pension
needs widowhood, which isn't a profile field, so the engine says "eligible" where the right answer is "maybe". Cards
show such conditions under "Also check" (`also_check`).

**Live run** (Gemini embeddings + Gemini Flash + bge reranker): fill in after setting `GEMINI_API_KEY` and
`RERANKER_URL`, re-indexing, then `python -m eval.run_eval`. The `eval-live` CI job runs it on pushes to `main`
when the `GEMINI_API_KEY` secret is set.

| Setup | Recall@5 | Faithfulness | Eligibility accuracy | Refusal (trick) | False refusals | p95 latency (s) |
| --- | --- | --- | --- | --- | --- | --- |
| Vector only | | | — | | | |
| + BM25 hybrid | | | — | | | |
| + Reranker | | | — | | | |
| + Rule engine + self-check | | | | | | |

Fixes made from the first offline run ("fix the worst failures"):

1. **Hindi questions could not reach English chunks** (Hindi recall 50%). Added cross-lingual query rewriting
   ([backend/rag/query.py](backend/rag/query.py)): each Hindi/Hinglish question is also searched as an English
   query (Gemini rewrite; welfare glossary offline) and both lists are fused with RRF. Hindi recall 55% → 95%
   (setup 4).
2. **"6000 रुपये किस योजना" was read as a scheme name** and refused. The name detector now stops at the first
   generic word ("किस", "wali", "koi"). Separately, a slightly negative cosine score triggered a refusal even
   with the threshold off. False refusals 19 → 0.
3. **"Digital Gaay Bima Yojana" matched real "… Bima Yojana" schemes.** A named scheme now needs ≥ 60% of its
   distinctive words in one hit's name. Trick refusals 8/10 → 10/10.
4. **Rules cut retrieval to an arbitrary top 15** when the profile was thin, which made setup 4 worse than setup 3.
   Rules now only exclude schemes that are definitely "no".
5. **Schemes with no extracted criteria showed as "eligible".** They are now "maybe" until criteria exist.

Load test (Day 6): `locust -f loadtest/locustfile.py --host <api> --users 50 --spawn-rate 5 --run-time 5m --headless --csv loadtest/results`,
then `python -m loadtest.report --api <api>` prints p50/p95/p99 per endpoint and cost per 1,000 queries from
`/metrics`.

---

## Deploy

The live setup is two Vercel projects (API + frontend) on free tiers:

| Piece | Where | Notes |
| --- | --- | --- |
| Database | Supabase (Postgres 17 + pgvector), **Session pooler** URL | the direct `db.<ref>.supabase.co` host is IPv6-only |
| Cache + rate limit | Upstash Redis via its REST API (`UPSTASH_REDIS_REST_URL` / `_TOKEN`) | `REDIS_URL` also works |
| LLM | Gemini: `gemini-3.5-flash` writes answers, `gemini-3.5-flash-lite` does profile / rewrite / rerank / self-check | free tier: 5 and 15 requests/min per model; each falls back to the other |
| Embeddings | `gemini-embedding-001` @ 768 dims | free tier: 100 texts/min; the embedder paces itself |
| Reranker | Gemini (flash-lite) scores the top 20 | the bge Space needs Hugging Face PRO for Docker; `reranker_space/` is ready if you upgrade |
| API | Vercel Python function ([api/index.py](api/index.py), [vercel.json](vercel.json)) | `render.yaml` + `Dockerfile` remain for Render/Docker hosting |
| Frontend | Vercel, root directory `frontend` | `NEXT_PUBLIC_API_URL` = the API URL |
| Tracing | Langfuse cloud | |

Steps:

1. **Database:** `python -c "from backend.rag.store import PgStore; from backend.core.config import get_settings; PgStore.init_schema(get_settings().database_url)"`
2. **Data:** `python -m scripts.load_hf_dataset --from-jsonl eval/corpus_sample.jsonl --state any`, then
   `python -m ingest.refresh --offline` (scraped pages) and `python -m ingest.verify apply-drafts`.
3. **API:** from the repo root, `vercel deploy --prod` (project `haqdar-api`), with the variables from `.env`
   (`DATABASE_URL`, `UPSTASH_*`, `GEMINI_API_KEY`, `LLM_*`, `LANGFUSE_*`, `SARVAM_API_KEY`, `ADMIN_TOKEN`,
   `RERANK_PROVIDER=llm`, `DB_POOL_MAX=2`, `CORS_ORIGINS`).
4. **Frontend:** from `frontend/`, `vercel deploy --prod` (project `haqdar-ai`) with `NEXT_PUBLIC_API_URL`.
5. **GitHub:** secrets `DATABASE_URL`, `GEMINI_API_KEY` (nightly refresh) and variable `API_URL` (keep-warm ping).

Check the free-tier limits on the day (Gemini quotas changed during the build) and use `/metrics` as the usage log.

---

## Repo layout and ownership

| Path | Owner | What |
| --- | --- | --- |
| `backend/rag/` | Person A | chunking, store (Postgres + in-memory), hybrid search + RRF, rerank, query rewrite, answers, self-check, indexer |
| `backend/agent/` | Person A | router, scheme resolver, state machine |
| `backend/api/` | Person A | FastAPI app and schemas |
| `backend/core/`, `backend/providers/` | Person A | config, cache, rate limit, tracing, usage; LLM, embeddings, reranker, speech interfaces |
| `eval/`, `loadtest/` | Person A | question set, four-setup eval, Locust |
| `scripts/load_hf_dataset.py` | Person A | Day 1 bootstrap corpus |
| `backend/rules/` | Person B | rule engine (reasons in en/hi/hinglish, `also_check`), profile extractor (understands short follow-up replies), follow-up picker (asks for the field that resolves the most results) |
| `ingest/` | Person B | myScheme scraper, parser, criteria extraction, daily refresh, verification workflow, drafted criteria |
| `frontend/` | Person B | Next.js installable web app: chat, cards, voice, checklist, updates panel, share link |
| `eval/profiles.json` | Person B | 10 personas with hand labels |
| `docs/` | Person B | demo script, pitch deck content, Day 1 decisions and the tasks only a person can do |

---

## Data credit and privacy

Scheme data: **myScheme.gov.in, Govt. of India**, via the community dataset
[shrijayan/gov_myscheme](https://huggingface.co/datasets/shrijayan/gov_myscheme) (Apache-2.0) and the own scraper; the HF data fills the gaps until the scraper has
re-crawled everything. Only eligibility fields reach the LLM or the session store. The source allowlist is `myscheme.gov.in`,
`*.gov.in`, `*.nic.in`.
