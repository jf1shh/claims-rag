# Portfolio hardening

Status: authorized by the maintainer's September 6 request to fix the review findings.

Keep one configured tenant per application. Every authenticated principal must match it;
claim authorization applies to document text, downloads, jobs, and mutations. Explicit empty
ACLs deny adjusters. Production defaults fail closed.

Use one application factory and lifecycle for the documented ASGI entry point and tests.
Share the ingestion queue. Heavy ingestion runs outside the event loop, with bounded bytes,
expanded archives, extracted text, and query sizes. Simulation is enforced server-side.

Audit metadata excludes question/answer text by default and supports concurrent durable appends.
Document source versions are immutable, staged before publishing index references; failed
publication must not replace the previous source/index. Preserve the existing filename scope
guard. This supersedes the earlier requirement to write source bytes only after DB commit:
writing a NEW immutable version before commit does not mutate the previous version.

Source responses expose version/chunk metadata and exact post-cap excerpts. A citation download
must reject stale versions instead of silently serving replacement bytes. Deletion removes
source versions; it is not a permanent archive. Check evidence again after context trimming.
Prompt delimiting is a mitigation, not an immunity guarantee. Do not invent a relevance
threshold without an evaluated baseline; document this residual model-quality limitation.

Fix deployment defaults, seed installation, login credential typing, privacy/error presentation,
retrieval setting propagation, evaluation reporting, and public CI execution. Add regression
checks that exercise actual factory construction, authenticated serving, concurrency and failure.
External IdP provisioning, production cutover, and claims correctness certification are outside
this remediation. Record unexecuted live evaluation and infrastructure checks explicitly.

## Fresh-runner verification

API contract tests must pass without cached model weights or model downloads. Postgres/HTTP/Locust orchestration smoke may explicitly substitute deterministic model boundaries, but must label synthetic timings and reject production latency assertions in that mode. The default performance benchmark retains real model execution.
