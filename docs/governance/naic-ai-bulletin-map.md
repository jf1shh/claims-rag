# NAIC AI Model Bulletin: governance evidence map

This maps ClaimsRAG to the NAIC Model Bulletin *Use of Artificial Intelligence Systems by Insurers*
(adopted by the NAIC Executive Committee and Plenary on December 4, 2023;
[source PDF](https://content.naic.org/sites/default/files/cmte-h-big-data-artificial-intelligence-wg-ai-model-bulletin.pdf.pdf)).
Item numbers below are the bulletin's own: Section 3 "AIS Program Guidelines" 1.0–4.0, and
Section 4 "Regulatory Oversight and Examination Considerations".

**What this is and isn't.** The bulletin is addressed to insurers, and each state adopts it (or not)
on its own terms. It asks an insurer for a written *AIS Program*. Software can't be compliant on its
own; it can only carry evidence an insurer's program would point to. So every item gets one of four
labels:

| Label | Meaning |
|---|---|
| **Built** | A control exists in this repo, and the evidence link pins it (see "How the evidence stays true"). |
| **Partial** | Some of the item is covered in code or docs; the rest is listed under [Gaps](#gaps-and-next-steps). |
| **Insurer** | An organisational duty (board oversight, committees, contracts, training). The repo can't supply it. |
| **Gap** | Neither the repo nor a documented deployment step covers it yet. |

This is an engineering evidence map, not legal advice, and it says nothing about any state's
adopted version. All claims, SOPs and documents in the repo are synthetic.

## Risk profile (Section 3 proportionality factors)

Section 3 asks that controls be "commensurate with" the insurer's own risk assessment, weighing five
factors. This is how ClaimsRAG stands on each:

| Factor | ClaimsRAG |
|---|---|
| (i) Nature of the decisions | A research aid for claim handlers. It answers questions over SOPs, endorsements, state rules and the claim file, and runs four review modes (supplement review, total loss, subrogation triage, SIU indicator checklist). Claim administration and fraud detection are both on the bulletin's life-cycle list (1.6), so it is in scope. It *supports* decisions; it records none. |
| (ii) Degree of potential harm | Moderate. A wrong figure or a missed SOP rule could lead a handler to over- or under-pay, or to mis-refer a claim to SIU. Hence the output checks under 1.1. |
| (iii) Human involvement | Every coverage, payment, denial, fraud, referral and reserve decision stays with a person. Answers carry `decision_status`, which defaults to `not_a_decision` ([`backend/contracts.py` `decision_status: Literal["not_a_decision"`](../../backend/contracts.py)). |
| (iv) Transparency and explainability | Every answer cites its source documents. Claim figures come from deterministic tools, not the model ([`backend/claims_tools.py` `def supplement_review`](../../backend/claims_tools.py)), and the model only explains them. |
| (v) Third-party reliance | The default stack runs open-weight models on the host. No document text or embedding leaves the machine (see [4.0](#40-third-party-ai-systems-and-data)). |

The consumer is the claimant, but the user is an internal handler. Nothing the system produces goes
to a consumer without a person deciding to send it.

## 1.0 General guidelines

| Item | Asks for | Status | Evidence |
|---|---|---|---|
| 1.1 | Mitigate the risk of Adverse Consumer Outcomes | **Built** | Four output-side checks run on live answers. The answer guard withholds answers that announce a claim outcome the record doesn't support, or that contain an off-allowlist URL or email ([`backend/answer_guard.py` `def apply_guard`](../../backend/answer_guard.py); default [`config.py` `"ANSWER_GUARD_MODE", "withhold"`](../../config.py)). The provenance check traces planted tokens and filenames back to their source ([`backend/provenance_check.py` `def unretrieved_filenames`](../../backend/provenance_check.py); default [`config.py` `"PROVENANCE_CHECK", "llm"`](../../config.py)). The figure check traces every amount, percentage and date in an answer to the computed section, the claim dossier or a source ([`backend/figure_check.py` `Deterministic figure check`](../../backend/figure_check.py)). The conflict check marks answers whose sources disagree as `conflicting_evidence` ([`backend/conflict_check.py` `def values_disagree`](../../backend/conflict_check.py); default [`config.py` `"CONFLICT_CHECK", "llm"`](../../config.py)). With no supporting context the router refuses instead of answering ([`backend/agentic_router.py` `def _no_context`](../../backend/agentic_router.py)). |
| 1.2 | Address governance, risk controls and internal audit | **Partial** | Governance: this map and [2.0](#20-governance). Risk controls: [3.0](#30-risk-management-and-internal-controls). The audit trail an internal-audit function would read is built ([2.3 d](#20-governance)). The internal-audit function itself is **Insurer**. |
| 1.3 | Senior management accountable to the board | **Insurer** | |
| 1.4 | Proportionate to use and to Degree of Potential Harm | **Built** | The risk profile above. Each output check has a setting so a deployment can tighten or relax it per use case ([`config.py` `ANSWER_GUARD_MODE must be withhold, flag, or off`](../../config.py)). |
| 1.5 | May be part of ERM, may adopt NIST AI RMF | **Insurer** | No NIST AI RMF crosswalk yet; see [Gaps](#gaps-and-next-steps). |
| 1.6 | Cover the insurance life cycle | **Built** | Use is limited to claim administration and fraud detection support (the four review modes and question answering). No underwriting, rating, pricing or marketing use. |
| 1.7 | Cover all AI-system life-cycle phases | **Partial** | Design: [docs/architecture.md](../architecture.md) (locked design decisions with reasons). Development and validation: [docs/build-history.md](../build-history.md), the eval suites under 3.4. Monitoring: the drift monitor compares each rerun with the last accepted run (3.3 c). No written retirement procedure (**Gap**). |
| 1.8 | Cover in-house and vendor systems | **Built** | Every model ClaimsRAG uses is listed under [3.3](#30-risk-management-and-internal-controls), with where it runs. |
| 1.9 | Notice to impacted consumers | **Insurer** | An internal tool; the insurer decides what notice claimants get. |

## 2.0 Governance

| Item | Asks for | Status | Evidence |
|---|---|---|---|
| 2.1 | Policies and procedures for each life-cycle stage | **Partial** | Engineering procedure: the session protocol in [CLAUDE.md](../../CLAUDE.md) (every failure recorded, every decision with reasons), [docs/release-checklist.md](../release-checklist.md), PR-only changes with required CI. Business policy for AI use is **Insurer**. |
| 2.2 | Documentation of compliance, written with Section 4 in mind | **Built** | This map, with its pinned evidence, and the [Section 4 index](#section-4-examination-documentation-index) below. Dated evaluation files are kept, not overwritten. |
| 2.3 a | Cross-functional committees | **Insurer** | |
| 2.3 b | Scope of authority, decisional hierarchies | **Built** (in software) | Four roles with explicit permission sets ([`backend/rbac.py` `PERMISSIONS`](../../backend/rbac.py)). An adjuster sees only assigned claims; supervisor, SIU and admin see all claims in the tenant ([`backend/rbac.py` `_ALL_CLAIMS_ROLES`](../../backend/rbac.py), [`backend/rbac.py` `def require_claim_access`](../../backend/rbac.py)). Purging a whole claim is admin-only ([`backend/rbac.py` `"claims:purge"`](../../backend/rbac.py)). |
| 2.3 c | Independence of decision-makers, lines of defence | **Partial** | The SIU role is read-only: it can investigate all claims but cannot upload, attach or delete ([`backend/rbac.py` `read-only investigation`](../../backend/rbac.py)). Organisational lines of defence are **Insurer**. |
| 2.3 d | Monitoring, auditing, escalation, reporting | **Built** | Every action that reads claim content or changes data writes an audit event, including denials: chat (streamed or not), upload, delete, document content and download, claim document listing, claim purge and the internal search endpoint ([`backend/api.py` `def _audit(`](../../backend/api.py)). The plain list endpoints (documents, claims, job status) are not audited. Each event carries subject, tenant, request id and a UTC timestamp, and the sink refuses an event without event, request id and tenant ([`backend/audit.py` `REQUIRED_FIELDS`](../../backend/audit.py)). Escalation: a withheld answer is replaced by a notice that it was "withheld for human review", and no generated text survives ([`backend/answer_guard.py` `withheld for human review`](../../backend/answer_guard.py)). |
| 2.3 e | Training and supervision | **Insurer** | Handler-facing write-ups for each review mode exist in [docs/workflows/](../workflows/README.md) and could seed training material. |
| 2.4 | Predictive-model procedures, error and bias detection | **Partial** | ClaimsRAG trains no model on historical claims data, so it holds no Predictive Model in the bulletin's sense (models that mine historic data to predict outcomes). Its models are pretrained retrieval and generative models, covered in 3.3 and 3.4. Error detection is built (3.4). Unfair-discrimination testing is a **Gap**. |

## 3.0 Risk management and internal controls

| Item | Asks for | Status | Evidence |
|---|---|---|---|
| 3.1 | Approval process; constraints and controls on automation | **Partial** | Automation limits are in code. `decision_status` defaults to `not_a_decision` (2.0 table above), and the review modes compute and cite but never decide. Production refuses to start with simulation answers or with the development sign-in ([`config.py` `SIMULATION_MODE must be false in production`](../../config.py), [`config.py` `AUTH_PROVIDERS must not include development in production`](../../config.py)). The approval process for adopting the system is **Insurer**. |
| 3.2 | Data practices: currency, lineage, quality, integrity, bias, suitability | **Partial** | Lineage: answers cite source documents, and the provenance and figure checks tie answer content back to them (1.1). Integrity: uploads are size-limited and filenames sanitised ([.claude/rules/backend-constraints.md](../../.claude/rules/backend-constraints.md)). Not covered: document currency (effective dates and superseded SOP versions aren't tracked) and bias analysis. Both are **Gaps**. |
| 3.3 a | Model inventory | **Built** | [Model inventory](#model-inventory) below. |
| 3.3 b | Development and use documentation | **Built** | [docs/architecture.md](../architecture.md), [docs/build-history.md](../build-history.md) (every phase, including failed attempts and why), [docs/current-state.md](../current-state.md). |
| 3.3 c | Interpretability, repeatability, robustness, reproducibility, traceability, drift | **Partial** | Robustness: adversarial suite (3.4). Reproducibility: a pinned-retrieval rerun of the golden set ([results-2026-10-20-34row-pinned.json](../../eval/results-2026-10-20-34row-pinned.json)). Traceability: citations plus the audit log. Drift: [`eval/drift_monitor.py` `RETRIEVAL_TOL = 0.03`](../../eval/drift_monitor.py) compares each golden, adversarial or retrieval-probe run with the last accepted baseline in [eval/drift_baselines.json](../../eval/drift_baselines.json), and a change of generation, judge or embedding model reports [`eval/drift_monitor.py` `not_comparable`](../../eval/drift_monitor.py) instead of a drift verdict ([docs/operations/drift-monitoring.md](../operations/drift-monitoring.md)), scheduled weekly via `.github/workflows/drift-monitor.yml` since 2026-10-22. |
| 3.4 | Validation, testing and retesting | **Partial** | A 34-question golden set scored by a local judge: context recall 0.983, reference-fact coverage 0.840 ([results-2026-10-21-34row-fts-or.json](../../eval/results-2026-10-21-34row-fts-or.json), [README Results](../../README.md#results)). An adversarial suite with a white-box held-out set: known-injection attack success 87.5% at baseline, 6% (1/16) with every defence on; held-out 25% (3/12) ([docs/adversarial-evaluation.md](../adversarial-evaluation.md)). The answer guard withheld 0 of 19 honest golden answers ([golden_guard_check.py](../../eval/golden_guard_check.py)). The test suite runs in CI on every PR. A protocol and tooling for expert review of live answers now exist ([docs/governance/expert-review-protocol.md](expert-review-protocol.md), [build_expert_review_packet.py](../../eval/build_expert_review_packet.py)), but no review has been run yet, and validation on real (not synthetic) claims still needs an insurer pilot. Both remain **Gaps** until a completed review is committed. |
| 3.5 | Protect non-public information, including access to the models | **Built** | Local-only processing: no document text or embedding goes to an external service (a critical constraint in [CLAUDE.md](../../CLAUDE.md)). Access control per role, claim and tenant (2.3 b, [`backend/tenant_context.py` `def require_tenant_scope`](../../backend/tenant_context.py)). The audit log drops question and answer text ([`backend/api.py` `fields.pop("answer", None)`](../../backend/api.py)) and is created owner-only ([`backend/audit.py` `os.O_APPEND, 0o600`](../../backend/audit.py)). Access logs redact document paths, which carry claimant surnames ([`backend/logging_setup.py` `class RedactDocumentPathsFilter`](../../backend/logging_setup.py)). Per-user rate limits ([`backend/rate_limit.py` `class SlidingWindowRateLimiter`](../../backend/rate_limit.py)) and security headers ([`backend/api.py` `X-Content-Type-Options`](../../backend/api.py)). S3 object storage can use a KMS key ([`config.py` `s3_sse_kms_key_id`](../../config.py)). |
| 3.6 | Data and record retention | **Partial** | Finished ingestion job rows expire after `JOB_RETENTION_DAYS` ([`config.py` `"JOB_RETENTION_DAYS"), 30`](../../config.py)). A whole claim can be purged, admin-only and audited, and the purge refuses while ingestion is in flight ([`backend/claim_purge.py` `class ClaimPurgeConflict`](../../backend/claim_purge.py), [`backend/api.py` `"claim_purge", outcome="purged"`](../../backend/api.py)). The audit log is append-only, fsynced per event, and recovers a torn last line without losing earlier records ([`backend/audit.py` `recovered_incomplete_tail`](../../backend/audit.py)). No retention or rotation schedule for the audit log or source documents (**Gap**; the period is the insurer's to set). |
| 3.7 | Predictive-model narrative | Not applicable | No Predictive Model (see 2.4). The equivalent narrative for the generative system is the [README](../../README.md). |

### Model inventory

| Role | Model (default) | Runs | Pinned at |
|---|---|---|---|
| Embedding (dense retrieval) | `all-MiniLM-L6-v2`, 384 dimensions | On the host CPU | [`config.py` `"EMBEDDING_MODEL", "all-MiniLM-L6-v2"`](../../config.py) |
| Reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` | On the host (`RERANK_PROVIDER=local`) | [`config.py` `"RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"`](../../config.py), [`config.py` `"RERANK_PROVIDER", "local"`](../../config.py) |
| Answer generation, conflict and provenance checks | Whatever model the local OpenAI-compatible server has loaded; the dated golden run used `Qwen3-Coder-30B-A3B-Instruct-GGUF` | Local server on the loopback address | [`config.py` `"LLM_BASE_URL", "http://127.0.0.1:13305"`](../../config.py) |
| Golden-set judge | A local model through the same kind of server | On the host | [ragas_lm_studio.py](../../eval/ragas_lm_studio.py) |

Keyword search (SQLite FTS5) and the claims calculations are deterministic code, not models.

## 4.0 Third-party AI systems and data

| Item | Asks for | Status | Evidence |
|---|---|---|---|
| 4.0 | Process for third-party data and AI systems | **Partial** | The default stack has no third-party *service*: every model in the inventory is open-weight and runs on the host. The integration points where a deployment can add a third party are the reranker endpoint (`RERANK_PROVIDER`, `RERANK_ENDPOINT`), the LLM endpoint (`LLM_BASE_URL`), S3 object storage and the OIDC identity provider ([docs/operations/local-and-production.md](../operations/local-and-production.md)). Each one is a vendor the insurer must diligence before switching it on. The model weights themselves are third-party artefacts; their provenance and licences are not yet recorded (**Gap**). |
| 4.1 | Due diligence on the third party | **Insurer** | |
| 4.2 | Contract terms: audit rights, regulator cooperation | **Insurer** | |
| 4.3 | Exercising audit rights | **Insurer** | |

## Section 4: examination documentation index

Where the documents a regulator may request (Section 4) would come from:

| Section 4 item | Source |
|---|---|
| 1.1 a–d The written AIS Program, its adoption, scope and proportionality | **Insurer**. This map supplies the system description, scope (1.6) and risk profile. |
| 1.1 e i (1) Constraints and controls on automation | 3.1 above. |
| 1.1 e i (2), 1.3 b Data governance: lineage, quality, integrity, bias, currency | 3.2 above (lineage and integrity built; bias and currency are gaps). |
| 1.1 e ii, 1.3 c ii (3) Measurements, thresholds and controls | Answer-guard, conflict and provenance modes and their measured effect ([docs/adversarial-evaluation.md](../adversarial-evaluation.md)); retrieval and answer metrics ([README Results](../../README.md#results)). |
| 1.1 e iii Protection of non-public information | 3.5 above. |
| 1.2, 2.1–2.3 Third-party diligence, contracts, audits | **Insurer**; integration points in 4.0 above. |
| 1.3 a Coordinating bodies | **Insurer**. |
| 1.3 c i Inventory of AI Systems | [Model inventory](#model-inventory). |
| 1.3 c ii (1) Compliance with program procedures | Audit log (2.3 d), PR history and CI runs on GitHub, [docs/build-history.md](../build-history.md). |
| 1.3 c ii (2) Data used in development and oversight | Synthetic corpus generators ([generate_auto_pdfs.py](../../generate_auto_pdfs.py), [create_sample_files.py](../../create_sample_files.py)) and the golden set ([golden_queries.py](../../eval/golden_queries.py)). |
| 1.3 d, 2.4 Validation, testing, auditing, model drift | 3.4 above; drift: 3.3 c and [docs/operations/drift-monitoring.md](../operations/drift-monitoring.md) (scheduled weekly since 2026-10-22). |

## Gaps and next steps

In rough order of value for a deployment:

1. **Scheduled drift monitoring** (3.3 c, Section 4 1.3 d) — **done 2026-10-22**. Runs weekly
   ([docs/operations/drift-monitoring.md](../operations/drift-monitoring.md), `.github/workflows/drift-monitor.yml`).
2. **Validation on real claims with expert review** (3.4). Every number so far comes from synthetic
   data. The expert-review half now has a protocol and tooling
   ([docs/governance/expert-review-protocol.md](expert-review-protocol.md)), but no review has been run
   yet. The real-claims half still needs a pilot with handler-reviewed answers on real files, under the
   insurer's data controls — out of reach in this repo.
3. **Bias and unfair-discrimination analysis** (2.4, 3.2). Check that output doesn't vary with
   claimant name when the claim facts are held constant. The deterministic rule tools in
   `backend/claims_tools.py` take no name/address input and aren't a bias surface; the free-text
   agentic path is, since it reads claim documents that name the claimant. A protocol and tooling now
   exist ([docs/governance/fairness-probe-protocol.md](fairness-probe-protocol.md),
   `eval/build_fairness_probe.py`), but no probe has been run yet. Address was ruled out of scope —
   the seeded claims have no claimant address separate from the loss location, and varying the loss
   location would change the facts under test.
4. **Document currency** (3.2). Track effective dates and superseded SOP and endorsement versions so an
   answer can't cite a retired rule.
5. **Retention schedule** for the audit log and stored documents (3.6), set by the insurer's records policy.
6. **Model provenance and licence register** (4.0), and a written retirement procedure (1.7).
7. **NIST AI RMF crosswalk** (1.5), if the insurer's program is built on it.

## How the evidence stays true

Each evidence link written as `` `path` `text` `` names a string that must appear in that file.
[tests/test_governance_map.py](../../tests/test_governance_map.py) checks every relative link in this
folder resolves and every pinned string is still in its file, including the defaults quoted from
`config.py`. If a control is renamed, removed or has its default changed, the test fails and this map
has to be updated in the same change.
