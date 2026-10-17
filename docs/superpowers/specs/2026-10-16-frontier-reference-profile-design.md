# Frontier reference profile: Claude as ceiling, local model as floor

**Status:** proposed, 2026-10-16 (owner idea, this spec drafted in session). Not built.
**Depends on:** per-role LLM endpoints (proposed task "Per-role LLM endpoints for multi-node deployment").
**Related:** `docs/enterprise-migration.md` target hardware (PR #14), `docs/architecture.md` Locked Design Decisions.

## Goal

Measure how much of ClaimsRAG's answer quality is limited by the model and how much by retrieval,
documents and prompts. Use Claude (Anthropic API) as a **frontier reference**: an upper bound for what a
much larger on-prem model could do. Keep the local model (Lemonade, single GPU) as the **floor**. A real
enterprise deployment (several DGX Spark units or high-memory Macs running open-weight models) is
expected to land between the two.

This is a measurement profile, not a deployment recommendation. On-prem buyers would run open-weight
models; Claude stands in for "a much stronger model", not for what they would deploy.

## Non-goals

- Changing the default profile. The default stays 100% local, and the README's "no hosted API calls"
  statement stays true for it.
- Sending anything but the synthetic corpus to the API. The profile refuses to start unless
  `APP_ENV` is `development` or `test`.
- Using Claude for embeddings or reranking. Those stay local; they aren't the bottleneck being measured.

## Questions it answers

1. **Model or system?** For each golden query, does the frontier generator pass where the local one fails
   (model-limited), or do both fail (retrieval, docs or prompt-limited)?
2. **Which roles need a big model?** Planner, synthesis, conflict extractor, judge: score each role on
   Claude with the rest local, to find the cheapest mix that holds quality.
3. **Do the defenses still matter?** Rerun the adversarial suite with a frontier generator: does injection
   success drop, does the answer guard still catch forged decisions, does the sandwich trade-off change?
4. **Is the judge the noise source?** Score the same local answers with a frontier judge and compare the
   run-to-run spread with the local judge's (0.798 / 0.825 faithfulness on 2026-10-16).

## Design

### 1. `AnthropicChatClient` (backend/llm_client.py)

A new `ChatClient` subclass beside `OpenAICompatibleClient`, implementing `models()`, `complete()` and
`complete_stream()` with the same `stage` argument. It uses the official `anthropic` Python SDK
(new pinned dependency), not an OpenAI-compatible shim.

- Selected by `LLM_PROVIDER=anthropic` (config.py's provider allowlist gains `anthropic`). Per-role
  overrides come from the per-role endpoints work, so e.g. only `synthesis` can be Anthropic.
- Credentials come from the SDK's normal resolution (`ANTHROPIC_API_KEY` or an `ant auth login`
  profile). Never logged, never in URLs, never committed. The owner provisions them; agents don't.
- Message mapping: the OpenAI-style `system` message moves to the top-level `system` parameter; the rest
  map to `user`/`assistant`. Retrieved sources stay inside the existing delimited source blocks.
- **Sampling:** current Claude models reject `temperature`/`top_p`/`top_k`. The adapter drops the
  `temperature` it receives and records that it did in the pipeline log. Runs can't be made
  deterministic this way, which is why the protocol below repeats runs.
- **Thinking and effort:** Claude Opus 5.5 can't turn thinking off, and its effort defaults to `medium`.
  Set `output_config.effort` explicitly per stage: `low` for the planner and conflict extractor, `high` for
  synthesis and the judge. Record the effort in the results file.
- **Refusals:** check `stop_reason` before reading content. Enable the server-side refusal fallback
  (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) and record in the results when a
  fallback model answered, so a score is never silently attributed to the wrong model.
- **Streaming:** `complete_stream()` uses `client.messages.stream(...)`, yielding text deltas so SSE keeps
  working.
- **Errors:** map the SDK's typed errors (`RateLimitError`, `APIStatusError`, `APIConnectionError`) to
  `ChatClientError` with retryable vs non-retryable kept distinct.
- **Prompt caching:** the system prompt is stable, but retrieved context changes on every query. Put
  `cache_control` on the system prompt only, and verify with `usage.cache_read_input_tokens`. Expect a
  small saving; don't design around it.
- The `engine` allowlist / SSRF constraint is unchanged: the Anthropic endpoint comes from server
  config and the SDK default, never from client input.

### 2. Frontier judge (eval/run_eval.py, eval/ragas_lm_studio.py)

- `get_lm_studio_judge()` stays as is. A sibling `get_anthropic_judge(model)` builds the Ragas
  `InstructorLLM` from `instructor.from_anthropic(AsyncAnthropic())`. **Verify against the installed
  `ragas==0.4.3` and `instructor` before writing it** (the repo's Authoritative Sources note says Ragas docs
  and the installed version disagree); if `InstructorLLM` won't take an Anthropic client, wrap it.
- `EVAL_JUDGE_PROVIDER` (`lemonade` default | `anthropic`) and `EVAL_JUDGE_MODEL` select the judge.
  `EVAL_APP_URL` already exists.
- Every results file records generator provider and model, judge provider and model, effort per stage,
  run date, and any fallback-served answers. Output: `eval/results-<date>-<generator>-<judge>-runN.json`.

### 3. Avoiding self-grading bias

A model judging its own answers inflates scores. Rules:

- **Never use the same model as both generator and judge** in a reported number. Pairings:
  local generator + Claude judge; Claude Sonnet 5.5 generator + Claude Opus 5.5 judge; local generator +
  local judge (the existing baseline). If Claude generates and Claude judges, the two must differ, and the
  results are labelled as same-family.
- Report correctness (scored against the verified reference) as the headline; it's the least
  judge-sensitive metric. Faithfulness is reported with its run-to-run spread.
- Spot-check: hand-review a sample of answers where the judges disagree by more than 0.3, and record the
  outcome. Human-reviewed cases are an open item in `docs/current-state.md` anyway.

## Run protocol

Each cell is two full runs on the 28-query golden set, plus the adversarial suite where marked:

| # | Generator | Judge | Adversarial | Purpose |
|---|---|---|---|---|
| A | local (Lemonade) | local | — | floor; exists (2026-10-16) |
| B | local | Claude Opus 5.5 | — | judge noise: same answers, stronger judge |
| C | Claude Sonnet 5.5 | Claude Opus 5.5 | yes | ceiling for generation |
| D | mixed: planner + conflict local, synthesis Claude Sonnet 5.5 | Claude Opus 5.5 | yes | "local where it fits" |

Retrieval scores (context precision/recall) don't depend on the generator; reuse them across cells and
say so.

**Cost:** each cell makes about 28 generator calls plus Ragas judge calls (several per query). Before any
paid run, measure one query's token usage, extrapolate, and get the owner's approval for the budget. No
paid run starts without that approval.

## Reporting

- A README section, "Floor and ceiling": a table of cells A–D with dates and models, which queries are
  model-limited vs system-limited, and the cheapest role mix that holds correctness.
- `docs/current-state.md` and `docs/build-history.md` entries per run, as usual.
- Wording rule: "frontier reference" or "ceiling", never "what an enterprise would get".

## Acceptance

- `LLM_PROVIDER=anthropic` works for chat and streaming; with it unset, behaviour and tests are unchanged.
- Unit tests mock the Anthropic client (no network, no key), covering message mapping, dropped
  temperature, refusal handling, fallback recording, and error mapping. The PR suite stays model-free.
- The profile refuses to start outside `development`/`test`.
- `crag check` passes with 0 blocking; `trail check` run.
- Results files carry generator, judge, effort and date; no reported number pairs a model with itself.

## Open decisions (owner)

- Budget ceiling for paid runs.
- Whether cell D's role mix is fixed up front or picked from cells B–C.
- Whether the "Floor and ceiling" README section ships before or after P3 (prompt-injection hardening).
