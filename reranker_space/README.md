---
title: Haqdar Reranker
emoji: 📑
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# Haqdar reranker (bge-reranker-v2-m3, CPU)

Cross-encoder reranker for Haqdar AI. The backend sends the top 20 fused candidates and keeps the top 5.

## Deploy

1. Create a new Hugging Face Space, SDK **Docker**, hardware **CPU basic (free)**.
2. Push the three files in this folder (`app.py`, `Dockerfile`, this `README.md`) to the Space repo.
3. Optional: add a Space secret `RERANKER_TOKEN`.
4. Set on the backend: `RERANKER_URL=https://<user>-<space>.hf.space` and `RERANKER_TOKEN=<same token>`.

```bash
curl -X POST "$RERANKER_URL/rerank" -H "Content-Type: application/json" \
  -d '{"query": "PM Kisan eligibility", "documents": ["PM-KISAN gives Rs 6000 a year to farmers", "Ujjwala gives LPG"]}'
```

Free Spaces sleep after inactivity; the backend falls back to Gemini scoring (then lexical) while it wakes.
