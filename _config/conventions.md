# Project Conventions

- Python 3.12 is the supported runtime target; keep the pinned dependency file authoritative.
- FastAPI request and response boundaries use Pydantic models.
- Retrieval behavior is covered by unit tests and the golden evaluation set.
- Uploaded content is untrusted input: validate paths, file types, sizes, and URLs at boundaries.
- Tenant and claim scope must be applied before retrieval, listing, download, or deletion.
- Local mode must remain runnable without cloud credentials.
- Every public behavior change updates the approved spec or records an explicit reason why the spec is unchanged.
- Verification reports include commands actually run and their observed output.
