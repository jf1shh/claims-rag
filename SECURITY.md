# Security

The repository contains synthetic data only. AutoClaimsRAG is a claims research assistant;
its answers are interpretations for human review, never completed coverage, fraud, payment,
denial, or referral decisions. Do not commit customer documents, credentials, runtime data,
model caches, or logs.

## Enforced boundaries

- Every `/api/*` endpoint authenticates and checks the principal against the application's
  configured `TENANT_ID`. One application serves one tenant; Postgres additionally scopes
  queries with tenant filters and RLS. Deploy a separate configured application per tenant.
- Permissions and claim ACLs protect listing, text, download, ingestion, jobs, and chat.
  An explicitly empty ACL denies adjusters. With no ACL file, development/test remain open;
  staging/production deny adjusters until assignments are configured.
- OIDC verifies the signature, issuer and audience and requires expiration, subject, issuer
  and audience claims. Service keys use `X-API-Key`. Browser credentials are kept in session
  storage for the current tab; live OIDC authorization-code login remains an integration task.
- Development authentication intentionally supplies an administrator without a credential.
  Docker Compose publishes this demo only on `127.0.0.1`. Never expose this profile publicly.
- Docker copies an explicit runtime file list and excludes `.env`, keys and credential files.
  Inject secrets at runtime. Git ignores are not Docker build-context exclusions.
- Actual request-body bytes, uploads, queries, expanded ZIP containers and extracted text are
  bounded. Complex-format parsing in HTTP and workers runs in a subprocess with a wall timeout; plain text uses bounded decoding. POSIX parser subprocesses
  additionally have a 2 GiB address-space limit. Embedding and inference still need appropriate
  deployment resource/concurrency limits. The per-principal limiter is synchronized locally,
  not distributed across server processes.
- Filenames and storage paths are confined to their storage root. Configured model/reranker
  endpoints are server-owned; request data cannot choose an outbound URL.
- Source bytes are staged under unique immutable keys before committing their index reference.
  Returned evidence carries document versions and chunk identifiers. Citation downloads using
  `?version=` reject a replaced version with 409 rather than silently serving replacement bytes.
  Pre-migration documents are explicitly `legacy-unversioned` until reingested.
- Synthesis checks for evidence again after context trimming. Source escaping/delimiting is a
  prompt-injection mitigation, not a proof that a model will ignore every malicious instruction.
  Nearest-neighbor retrieval is not a calibrated relevance test; nonempty matches do not alone
  establish that a question is answerable. This requires ongoing adversarial/model evaluation.
- JSON and final SSE answers include a validated `structured` evidence/interpretation/decision
  boundary projection. Legacy answer fields remain available. This validates the representation,
  not the truth of every model-generated assertion. Simulation can be disabled and cannot then
  be selected or used as a provider-failure fallback.
- Audit JSONL appends are serialized across threads/processes and fsynced. Records contain actor,
  tenant, request ID, filenames/sources and outcomes, excluding chat question/answer text by
  default. This is an application append discipline, not tamper-proof/WORM storage. Operators
  control log access, retention and archival. A torn trailing append is recovered on next write.
- API responses disable caching; unexpected errors use generic messages, not exception details.
  The frontend uses system font fallbacks and makes no Google Fonts requests.
- Pull-request CI and public-repository CI use GitHub-hosted runners. The personal runner is
  eligible only for pushes to a private repository. Hosted PR reviews remain disabled.

## Data lifecycle and deployment limitations

The local inference profile keeps document processing on the host. Configured remote inference,
reranking, S3 and OIDC profiles communicate with their configured services; document/query data
can leave the host in those profiles. TLS, provider access controls, encryption at rest, backups,
audit retention and incident response are deployment responsibilities, not claims of this demo.

Deleting a document removes its index and current source. Replacement and interrupted publication
can leave unreferenced versions. Stop API/writers, then run
`python scripts/collect_source_garbage.py` to inspect the count and add `--apply` to remove them.
This works for filesystem and S3 adapters and retains currently referenced keys. Successful async
jobs remove their private staging bytes; failed/interrupted staging and external S3 object versions
need an operator retention policy. Clear staging only when no queued/retry jobs require it. Deletion
from this application does not delete backups, independent copies, S3 version history, or audit logs.

## Reporting

Use a private repository security advisory or contact the maintainer privately. Include impact,
a synthetic reproduction, affected version, and a suggested mitigation if available. Do not publish
real documents or credentials in an issue. Maintainers should prioritize isolation and disclosure
risks, coordinate fixes, and record their actual verification evidence.
