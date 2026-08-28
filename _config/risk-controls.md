# Risk Controls

| Risk | Required control | Verification evidence |
|---|---|---|
| Ungrounded answer | Refuse synthesis without authorized supporting evidence | Zero-context test and golden evaluation |
| Cross-claim leakage | Scope every retrieval and document operation by claim | Isolation tests |
| Cross-tenant leakage | Resolve tenant from verified principal, not request data | Negative-access tests and database policy tests |
| Path traversal | Confine normalized storage keys to the configured root | Traversal and symlink tests |
| SSRF through model/provider configuration | Allowlist configured providers and validate outbound targets | URL validation tests |
| Oversized or malformed upload | Enforce byte/type/text limits before expensive processing | API abuse tests |
| Schema drift | Version and test migrations from an empty database | Migration checks |
| Untraceable answer | Return evidence IDs, source versions, and locators | Contract tests |
| Autonomous decision confusion | Emit an explicit decision boundary defaulting to `not_a_decision` | Response contract tests |
| Guardrail rot | Self-test each promoted rule and trace it to a lesson | Harness self-tests |
