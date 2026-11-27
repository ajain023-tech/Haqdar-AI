# Pitch deck — slide content

Eight slides; numbers marked ⟨…⟩ come from the live eval and the Day 6 load test (`eval/results/latest.md`,
`python -m loadtest.report`). Build the slides from this file.

---

## 1. Haqdar AI — *Hakk se, apni yojana*

A free, voice-first assistant that tells any citizen which government schemes they qualify for, why, and which
documents to bring, in Hindi, Hinglish or English, with an official link on every answer.

## 2. The problem

* Over 2,000 central and state schemes on myScheme alone, written mostly in English, often as PDFs.
* The people they're for — farmers, widows, street vendors, first-generation students — are the least likely to
  find or read them.
* Generic chatbots guess eligibility and invent schemes. A wrong "yes" costs a trip to the office and a lost day's wage.

## 3. Demo

Ramesh (Hindi voice, farmer in UP) → Priya (Hinglish, a follow-up changes her results live) → a trick question
about a scheme that doesn't exist → "Recently updated" panel and document checklist. See `docs/demo-script.md`.

## 4. How it works

* **Rules decide, the LLM explains.** Eligibility is a deterministic check of the user's profile against
  structured criteria. The LLM only explains the result.
* **Every claim is cited.** A self-check maps each sentence to a retrieved official chunk; anything unsupported is
  removed, and weak evidence gets "I couldn't find this in official sources".
* **Fresh data.** A daily job re-checks every scheme, re-processes only what changed, and shows a verification
  badge (verified / auto-extracted / needs re-check) and a last-checked date on every card.
* Architecture diagram: README → Architecture.

## 5. Results (100 questions: 40 Hindi, 30 Hinglish, 30 English + 10 trick questions)

| Setup | Recall@5 | Faithfulness | Eligibility accuracy | Trick questions refused | p95 latency |
| --- | --- | --- | --- | --- | --- |
| Vector only | ⟨ ⟩ | ⟨ ⟩ | — | ⟨ ⟩ | ⟨ ⟩ |
| + BM25 hybrid | ⟨ ⟩ | ⟨ ⟩ | — | ⟨ ⟩ | ⟨ ⟩ |
| + Reranker | ⟨ ⟩ | ⟨ ⟩ | — | ⟨ ⟩ | ⟨ ⟩ |
| + Rule engine + self-check | ⟨ ⟩ | ⟨ ⟩ | ⟨ ⟩ | ⟨ ⟩ | ⟨ ⟩ |

Offline pipeline check today (no LLM): recall@5 95%, eligibility accuracy 98% on 10 personas, 10/10 trick
questions refused, 0 real questions wrongly refused. Replace with the live numbers.

## 6. Built to scale

* Stateless API, sessions in Postgres/Redis → add instances behind a load balancer.
* Metadata pre-filter (state, category) before vector search keeps queries fast at 10,000+ schemes.
* A new state = new data + a filter value; a new language = prompts + a speech setting.
* Every model (LLM, embeddings, reranker, speech) sits behind one interface: swap Gemini for an open model in config.
* When API Setu access is approved, only the ingestion source changes.

## 7. Cost

* ₹0 to build and run on free tiers (Render, Supabase/Neon, Upstash, Gemini, Hugging Face Spaces, Vercel).
* Rules before LLM: eligibility is a database query. Repeated questions come from the cache, with no LLM call.
* Measured cost at paid-tier prices: ⟨$ per 1,000 queries⟩ (load test, 50 concurrent users, p95 ⟨ ⟩ s).
* Citizens never pay. Running costs: CSR funding, government innovation grants, and a paid dashboard for NGOs and
  Common Service Centres that serve many people.

## 8. Next

WhatsApp bot · one more regional language · "do my documents qualify?" (OCR) · alerts when a new scheme matches a
saved profile · NGO/CSC dashboard · API Setu official feed.

*Unofficial assistant. Final eligibility is decided by the department. Data: myScheme.gov.in, Govt. of India.*
