# AutoClaimsRAG

A local-first, agentic RAG system for auto insurance claims handling — built to show what happens when domain expertise and modern retrieval/agentic AI techniques compound instead of substitute for each other.

## Why this exists

Insurance claims handlers spend a meaningful share of every day hunting through scattered PDFs, spreadsheets, and adjuster guides for the one fact that resolves a claim: a labor rate cap, an exclusion clause, a rider's eligibility window. That friction compounds under real regulatory variance (every state has different statutes and caps) and under real time pressure (claims carry SLAs). AutoClaimsRAG was built by an insurance claims/appraisal professional with 17 years in the industry, aimed specifically at that problem — not a generic document chatbot, but a system whose evaluation and design decisions are shaped by what actual adjusting judgment calls look like: exclusion stacking, regional rate caps, SIU fraud patterns, endorsement math, subrogation eligibility.

Everything in this repo runs on synthetic, generated seed data — no proprietary or confidential content of any kind.

## What it does

- Natural-language search over auto insurance guidelines, endorsements, state statutes, and adjuster reports (PDF/DOCX/XLSX/TXT)
- Per-claim document scoping — upload a claim's own dossier (police report, telematics, shop estimates) and query it alongside global policy documents in the same conversation
- An agentic router that plans multi-step retrieval, self-corrects when the first pass comes back empty, and **refuses to answer rather than let the model fabricate one** when nothing relevant was found
- Runs entirely locally: embedding, reranking, vector search, and generation (via LM Studio) all execute on-device — no document content or query ever leaves the machine

## Architecture

```
Documents (PDF/DOCX/XLSX/TXT)
        │
        ▼
DocumentParser ──▶ TextChunker (parent/child: 1200/200 + 250/50)
        │
        ▼
EmbeddingEngine (all-MiniLM-L6-v2, local)
        │
        ▼
SQLiteVectorStore
  ├─ dense vector search (child chunks, in-memory normalized cache)
  ├─ FTS5 keyword search (parent chunks)
  └─ Reciprocal Rank Fusion ──▶ RerankingEngine (cross-encoder, local)
        │
        ▼
AgenticRAGRouter
  plan (decompose query) → retrieve (per sub-query) → self-correct (if empty) → synthesize
        │
        ▼
FastAPI ──▶ frontend (claims queue, per-claim folders, chat, pipeline logs)
```

### Retrieval: hybrid, not just vector

Dense embeddings are good at paraphrase and bad at exact structured lookups — a query like "what's the comprehensive deductible" can under-rank a deductibles *spreadsheet* in favor of a narratively-similar case study PDF, purely because prose embeds "closer" to a natural-language question than a table does. Keyword search (SQLite FTS5) catches exactly this case. The two are fused with **Reciprocal Rank Fusion**, which combines rank position from both signals without needing to calibrate incompatible similarity scores against each other, and the fused candidate pool is reordered by a local cross-encoder reranker for the final top-k.

### Agentic planning and self-correction

Each query goes through a planner that decides whether it needs global policy documents, the active claim's dossier, or both, and decomposes it into targeted sub-queries. If retrieval comes back empty, a self-correction step retries with a rewritten query before giving up. If it's *still* empty, synthesis is skipped entirely — the system returns "I couldn't find supporting documents" rather than let the LLM answer from its own knowledge and cite sources that don't exist. That last guard exists because the eval harness (below) caught exactly that failure mode in production.

### Performance

Vector search runs against an in-memory, pre-normalized embedding matrix cache instead of re-reading every embedding BLOB from SQLite and recomputing corpus norms on every query — the naive version of that is a real bottleneck that scales with corpus size, not query count.

## Evaluation — because "it looks right" isn't good enough

Most RAG demos never measure anything past a handful of manually-eyeballed examples. This one does, and it's the part of the project I'd point a hiring manager to first.

**The golden set is domain-grounded, not generic FAQ.** 19 queries covering exclusion stacking (DUI + consequential damage), regional labor rate caps, SIU fraud red flags, OEM/LKQ parts rider eligibility, deductible/endorsement math, and subrogation — plus one deliberate hallucination probe (asks about a DUI citation for a claim where no such document exists in the corpus, specifically to check whether the system fabricates evidence or correctly says it isn't there). Every reference answer was verified against the actual text in the underlying documents, not against what the demo mode claims — which turned out to matter (see below).

**Retrieval quality**: naive (vector-only) vs. the app's real hybrid+FTS5+RRF+cross-encoder pipeline, scored with Ragas Context Precision/Recall.

**Groundedness**: Faithfulness of the live agentic router's actual generated answers, judged against everything that really grounded them — both the retrieved sources and the claim dossier context injected into the prompt.

**The judge model runs locally too** — Ragas is wired to LM Studio via an OpenAI-compatible client, so evaluation makes zero calls to any hosted API, consistent with the rest of the system.

### Results

![Evaluation results: Context Precision and Recall for naive vs. hybrid+rerank retrieval, and Faithfulness across the dossier-scoring fix and the corpus rebuild](assets/eval_results.png)

| Metric | Naive | Hybrid + Rerank |
|---|---|---|
| Context Precision | 0.754 | 0.807 |
| Context Recall | 0.882 | 0.916 |
| Faithfulness (live answers) | — | 0.854 |

The aggregate retrieval gap understates the story. 12 of 19 queries are single-fact global lookups both methods ace near-perfectly — the interesting signal is in the 7 claim-scoped queries:

- **Clear hybrid wins**: a claim-scoped shop-estimate query where naive completely missed the source document (0.0 recall) while hybrid retrieved it with near-perfect precision and recall; a police report query where naive's top-1 was an image file with no real text content, correctly demoted by reranking (naive 0.5 → hybrid 1.0 precision).
- **Reported honestly, not cherry-picked away**: hybrid was clearly *worse* on two queries — one where naive reached 1.0 precision and hybrid stalled at 0.5, another where hybrid's recall dropped to 0.4 (naive 0.75) after pulling in a topically-adjacent-but-wrong document. Real regressions, not smoothed over.
- **The most important finding wasn't a hybrid-vs-naive story at all**: one query — "does this claim's stolen equipment exceed the endorsement cap, and by how much" — needs *two* documents (the endorsement's cap *and* the claim-specific receipt total) surfaced together, and single-shot top-k retrieval failed to do that in *either* mode, before or after the corpus rebuild. That's not a retrieval-tuning gap, it's the exact structural reason the agentic planner's query decomposition exists (see Phase 9 in the fixes below) — this eval validates the architecture's design rationale, it doesn't just score the retrieval layer in isolation.
- **Faithfulness rose 0.735 → 0.811 → 0.854** across two distinct fixes: first when the harness was corrected to score against everything the model was actually grounded in (see bug #3 below), then again after a full corpus rebuild fixed a chunking bug and restored ~42 documents that had silently been saved as fake binaries (see "Bugs this eval harness actually found" in the codebase history). Two different fixes, two independent, honest improvements — not one number tuned repeatedly until it looked good.

### Bugs this eval harness actually found and fixed

An eval harness earns its keep by catching real defects, not by producing a nice-looking dashboard. This one caught four, in four different layers of the system:

1. **A real hallucination path in the production router.** A global policy query got misclassified by the planner (`needs_global_policies: False`) while no claim was active, which also short-circuited the claim-dossier retrieval branch — both retrieval paths got skipped, and the LLM synthesized a confident answer citing filenames that don't exist anywhere in the corpus. Root-caused, fixed with a fallback path plus a hard stop against zero-context synthesis, verified.
2. **The simulated demo mode's hardcoded narrative contained facts the actual source documents don't support** — a coverage cap stated as $5,000 where the real document says $3,500, a rider eligibility threshold stated as 3 years where the real document says 5 years, a fabricated hour cap cited to a document that doesn't contain it, and a claim decision that mischaracterized a legitimate repair line item as a duplicate charge to reject. Found by cross-checking the golden set's reference answers against the real documents; corrected.
3. **A measurement gap in the eval harness itself.** Faithfulness scoring initially checked answers only against the `sources` field returned by the API — but the router also grounds claim-scoped answers in a claim summary dossier injected directly into the prompt, which isn't a search result and wasn't in `sources`. Correct, dossier-grounded answers were scoring as unfaithful. Fixed by surfacing the dossier as its own field in the API response and scoring against it too.
4. **A genuine multi-hop retrieval gap, and a second bug it exposed.** One query needed two documents — an endorsement's coverage cap *and* a claim's own itemized receipt — surfaced together to compute correctly. Single-shot semantic search wasn't guaranteeing both made it into context; the receipt kept losing its slot to a more topically-similar policy document. Fixed by always including a claim's own documents directly rather than making them compete semantically for a slot (claim dossiers are small — a handful of documents — so there's no retrieval-quality tradeoff to make). That fix then exposed a *second*, different problem: with the correct receipt now in context, the LLM still calculated from only one of its two line items and reached the wrong total. That answer was fully **faithful** to the context it used — it just didn't use all of it, which Faithfulness doesn't check. A one-line prompt instruction ("enumerate every item before totaling") fixed it, verified by direct before/after comparison, since this class of error doesn't move the Faithfulness score at all.

## Try it locally

```powershell
# 1. Create and activate a virtual environment, then install dependencies
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. Generate synthetic seed guidelines and ingest them
.venv\Scripts\python generate_auto_pdfs.py
.venv\Scripts\python ingest_all.py

# 3. Start the backend (serves the frontend too)
.venv\Scripts\python -m uvicorn backend.app:app --reload --port 8000
# → http://localhost:8000

# 4. (Optional) Point LM Studio at port 1234 with any OpenAI-compatible chat model
#    loaded, then run the eval harness:
.venv\Scripts\python eval/run_eval.py
```

Without an LM Studio server running, the app falls back to a rule-based simulation mode so the UI and retrieval pipeline are still fully explorable.

## Known limitations

- **Faithfulness measures groundedness, not correctness** — a real distinction, not a caveat unique to this project. An answer that uses only part of the available context can score perfectly faithful while still reaching the wrong conclusion (see bug #4 above). Don't infer answer correctness from a high Faithfulness score alone on multi-fact queries.
- `eval/results.json`'s Context Precision/Recall numbers reflect single-shot retrieval via a debug endpoint, not the full agentic pipeline's guaranteed claim-document inclusion — the two are deliberately isolated so retrieval-strategy comparisons aren't confounded by pipeline-level behavior. An answer-correctness metric to quantify the multi-hop fix directly is unbuilt.
- Faithfulness scoring is bounded by the local judge model's own reasoning quality — a real tradeoff of local-only evaluation against a larger hosted judge, made deliberately to keep the whole pipeline (including evaluation) consistent with the "runs entirely locally" design constraint.
- The demo claim fixtures are currently duplicated between the backend and frontend rather than served from a single source of truth.

## Tech stack

FastAPI · SQLite (custom hybrid vector + FTS5 store) · sentence-transformers (`all-MiniLM-L6-v2`) · cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) · LM Studio (local OpenAI-compatible inference) · Ragas (local evaluation) · vanilla JS frontend

## About

Built independently by [Jared Fisher](https://www.linkedin.com/in/jaredf-17680b7a) — 17 years in auto insurance claims and appraisal — as a demonstration of applying domain expertise directly to RAG and agentic AI system design, evaluation, and debugging.
