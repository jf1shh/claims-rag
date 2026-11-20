# Hard golden group (2026-11-20)

Follow-up to the 2026-10-21 null result (`docs/current-state.md`): the original 33 golden rows are all served at top_k=4, so no retrieval change can move them. This adds a separate **hard group** and measures it with the no-LLM probe (`scripts/probe_retrieval.py`; no judge model, no GPU).

## What was added
- `eval/golden_hard_queries.py`: 19 rows (14 near-miss, 2 multi-source, 3 hand-reworded paraphrases of original documents).
- `eval/hard_corpus/`: 11 synthetic `.txt` documents added on top of the 48-document corpus: superseded 2025 editions (storage fees, labor rates, total-loss valuation), a sibling rider (roadside Basic), another state's addendum (diminished value Texas and Florida), similar form numbers (tire and wheel T210 and T120, rental R15), and two salvage SOPs.
- The probe reports the original rows first and unchanged, then the hard group on its own line. Rows may list `avoid` documents; `distractor_outranks` counts rows where a near-miss ranks above every expected source.
- Rows and documents were written before any measurement. No row was removed or edited after measuring.

## Result (current main pipeline, all-MiniLM-L6-v2, cross-encoder, pool 15)
Two identical runs differ by 0 (JSON compared apart from ingest seconds).

Fact matching is by whole number (`_fact_in`: "8" does not match inside "18" or "0.8"). The first version of this table used a plain substring match; re-run with the whole-number match, the original-33 fact recall fell from 0.8837 to 0.8488 (pool 4: 0.8721 to 0.8372) and nothing else moved. Three facts flipped, all true corrections (the fact was not in the served text as its own number): `brooks-siu-checklist` fact 20 (matched inside 2019, 2021, 2026, $7200) and fact 6 (inside $6700, 2026); `webb-subrogation-triage` fact 1000 (inside $100000). The metric got stricter; the retrieval did not change (ranks and served sets are identical). The drift baseline was re-accepted on 2026-11-20 as `eval/probe-2026-11-20.json` (original 33 rows). Hard-group numbers, including fact recall 1.000, are unchanged. The 2026-10-21 baseline (`eval/probe-2026-10-21.json`, fact recall 0.8837) was measured with the substring match and is not comparable to these fact-recall figures.

| Run | Original 33: in top4 / served / MRR / fact recall | Hard 19: in top4 / served / MRR / fact recall / distractor outranks |
|---|---|---|
| main (pool 15) | 30 / 33 / 0.8131 / 0.8488 | 19 / 19 / 0.8571 / 1.000 / 3 |
| pool 8 | 30 / 33 / 0.8162 / 0.8488 | 19 / 19 / 0.8571 / 1.000 / 3 |
| pool 4 | 31 / 32 / 0.8131 / 0.8372 | 19 / 19 / 0.8571 / 1.000 / 3 |
| bge-small-en-v1.5 | 30 / 33 / 0.8120 / 0.8488 | 19 / 19 / 0.8571 / 1.000 / 3 |
| no reranker | 31 / 32 / 0.7894 / 0.8488 | 19 / 19 / 0.8254 / 1.000 / 3 |

## Reading it
The repair is **not achieved**. On honest hard rows the pipeline is still at the ceiling: every expected source is served (19/19) and every reference number is in the served text. The only spread is rank order (3 rows where a near-miss document ranks first: 2026 labor rates and Florida total loss, each behind its 2025 edition, and the general diminished-value guideline behind the state addenda). No knob moved a hard-group number by 0.05; the largest was the reranker off (MRR -0.032). Fact recall by number presence cannot see a wrong-document answer when the right one is also served.

What this means: the corpus and the top-4 window are too forgiving to separate retrieval changes, even with near-miss documents. A separator needs rows where the right answer is not in the top 4 (a larger corpus or tighter served window), or a check of which document the answer *used* (needs the judge model). Neither was done here.

Not measured: the judge-scored metrics (Ragas) and any GPU run.
