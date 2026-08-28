# Security

AutoClaimsRAG handles claims-like documents and must treat all uploaded content as sensitive, untrusted input. The repository contains synthetic data only; never commit real claim files, credentials, runtime databases, model caches, or logs.

## Current security boundaries

- Local mode is single-user and uses an explicit development identity.
- Production deployments must resolve tenant context from verified authentication, not client-controlled claim IDs or filenames.
- Retrieval, listing, download, and deletion must be tenant- and claim-scoped.
- Uploaded filenames and storage keys must be normalized and confined to the configured storage root.
- Model/provider endpoints must be configured server-side and validated; request data must never select an arbitrary outbound URL.
- Upload bytes, extracted text, query length, and retrieval counts must be bounded.
- The assistant must refuse synthesis without supporting evidence.
- Assistant output is an interpretation for human review, not an autonomous coverage, fraud, payment, denial, or referral decision.
- Production errors must not disclose stack traces, filesystem paths, credentials, or raw sensitive document content.

## Reporting a vulnerability

Please do not open a public issue for an undisclosed vulnerability. Use a private repository security advisory or contact the maintainers privately with:

- A concise description and impact.
- A minimal reproduction that uses synthetic data where possible.
- Affected commit/version.
- Suggested mitigation, if known.

Maintainers should acknowledge reports, assess tenant-isolation and evidence-exfiltration risk first, and publish a coordinated fix when appropriate.

## Security verification

Security-sensitive changes require focused tests plus the repository security gate. At minimum, verify traversal, SSRF, oversized input, malformed files, cross-claim access, cross-tenant access, secret scanning, dependency auditing, and safe production error responses.
