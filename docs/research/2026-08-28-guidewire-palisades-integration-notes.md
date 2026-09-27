# Guidewire Palisades Integration Notes

**Date:** 2026-08-28  
**Scope:** Public Guidewire Palisades documentation and official Guidewire material reviewed for useful ClaimsRAG design patterns.

## Source limitations

The Palisades documentation landing page is a dynamic documentation shell and exposed little readable content through the research fetcher. The official Palisades ClaimCenter release-highlights page and official Guidewire integration material were readable. Individual API reference pages may require interactive login or dynamic rendering. The findings below distinguish directly observed facts from design implications.

## Confirmed official findings

### 1. ClaimCenter Cloud API is versioned and authorization-aware

The official Palisades ClaimCenter release highlights identify **Cloud API for ClaimCenter version 1.15.0** and list new endpoints. The page explicitly says that when doing a core update, customers may need to add endpoints to appropriate `role.yaml` files to provide authorization to users and services.

Source: [What's new in ClaimCenter for Palisades](https://docs.guidewire.com/cloud/palisades/whatsnew/topics/cc-release_highlights.html)

Useful implication: an ClaimsRAG integration should treat upstream API versions and endpoint permissions as explicit configuration and deployment artifacts, not assume that a token automatically grants access to every claim/document operation.

### 2. ClaimCenter exposes lifecycle-style operations, not just read-only records

The Palisades release page lists bulk invoice resources and custom actions including `validate`, `submit`, `request-stop`, `request-void`, and `retry`, as well as claim-scoped concurrent-employer resources.

Source: [What's new in ClaimCenter for Palisades](https://docs.guidewire.com/cloud/palisades/whatsnew/topics/cc-release_highlights.html)

Useful implication: future ClaimsRAG adapters must distinguish retrieval/read operations from commands or state-changing actions. Commands should require explicit human approval, idempotency, audit events, and command/result correlation.

### 3. Guidewire describes an API-first integration framework

Guidewire’s official integration-framework page describes connecting external applications to and from Guidewire Cloud and notes that insurers may have many policy, billing, and claims integrations.

Source: [Guidewire Integration Framework](https://www.guidewire.com/de/developers/developer-tools-and-guides/integration-framework)

Useful implication: ClaimsRAG should provide a provider-neutral integration adapter and avoid embedding carrier-specific logic inside retrieval or answer generation.

### 4. Guidewire App Events support downstream event-driven workflows

Guidewire’s official App Events article states that App Events are generally available for ClaimCenter and PolicyCenter Cloud customers. It describes business events being published to downstream systems, subscriptions configured through an admin UI or Integration Gateway, and downstream actions triggered by claim or policy lifecycle events. The article gives fraud scoring and policyholder notification as example downstream scenarios.

Source: [Simplify Event-Driven Integrations on Guidewire Cloud with App Events](https://www.guidewire.com/de/resources/blog/technology/simplify-event-driven-integrations-on-guidewire-cloud-with-app-events)

Useful implication: ClaimsRAG should support event-driven ingestion and re-evaluation instead of polling every claim. Relevant event types might include claim created/updated, document added, exposure updated, invoice changed, or claim status changed, with tenant and claim scope carried through the event envelope.

### 5. The Palisades release includes new document-related API surface elsewhere in the platform

Guidewire’s public release search result for the Palisades platform describes new ClaimCenter API endpoint support for matters, documents, claims reopen, and exposure reopen. The accessible ClaimCenter release-highlights page specifically exposed the version and bulk-invoice additions, but not the complete endpoint details.

Source: [Guidewire Cloud Platform releases](https://www.guidewire.com/products/technology/guidewire-cloud-platform-releases)

Useful implication: document synchronization should be built as a versioned, metadata-aware adapter rather than assuming document files are available through one fixed endpoint. Exact endpoint semantics must be verified against the authenticated API reference before implementation.

## What is not established

- The public sources reviewed do not establish which insurers use Palisades or ClaimCenter. Customer-specific usage should not be claimed without a reliable company or Guidewire source.
- The sources do not establish that ClaimsRAG can access a customer’s ClaimCenter tenant without customer-provided credentials, permissions, and an approved integration arrangement.
- The sources reviewed do not provide enough authenticated API detail to implement a production ClaimCenter connector safely.

## Design changes recommended for ClaimsRAG

### A. Add an upstream integration boundary

```python
class ClaimsSystemAdapter(ABC):
    def get_claim(self, tenant_id: str, claim_id: str) -> ClaimSnapshot: ...
    def list_documents(self, tenant_id: str, claim_id: str) -> list[RemoteDocument]: ...
    def get_document(self, tenant_id: str, document_id: str) -> bytes: ...
    def execute_command(self, command: ClaimsCommand) -> CommandResult: ...
```

The first production implementation should be read-only. State-changing commands are a later phase and must not share the retrieval path.

### B. Define an event envelope

```json
{
  "event_id": "evt_...",
  "event_type": "claim.document_added",
  "occurred_at": "2026-08-28T00:00:00Z",
  "source": "guidewire-claimcenter",
  "source_version": "1.15.0",
  "tenant_id": "tenant-...",
  "claim_id": "claim-...",
  "resource_id": "document-...",
  "resource_version": "...",
  "idempotency_key": "..."
}
```

Event consumers must authenticate the publisher, validate the schema, enforce tenant/claim scope, deduplicate by `event_id` or idempotency key, and enqueue a bounded ingestion/re-evaluation job. Event payloads should contain metadata and references rather than unnecessary document contents.

### C. Add external-system provenance to ICM evidence

Evidence from ClaimCenter should carry:

- `source_system`: `guidewire-claimcenter`.
- `source_api_version`.
- Remote resource/document ID.
- Remote resource version or modified timestamp.
- Synchronization timestamp.
- Local checksum and local document version.
- Authorization scope used for retrieval.

This makes the Evidence → Interpretation → Decision boundary auditable across systems.

### D. Add connector-specific gates

Before enabling a ClaimCenter adapter:

1. Read-only access is proven against a synthetic or vendor-provided test tenant.
2. API version and role permissions are pinned and tested.
3. Tenant and claim isolation tests pass.
4. Remote document versions and deletion events are handled.
5. Event replay and duplicate delivery are safe.
6. Remote rate limits, timeouts, retries, and circuit breaking are tested.
7. All remote reads and commands are audited.
8. No state-changing endpoint is exposed through chat without an approval gate.

## Recommended roadmap placement

- **Foundation:** define `ClaimsSystemAdapter`, remote provenance fields, event envelope, and read-only contract tests using fakes.
- **Phase 1/2:** implement a read-only ClaimCenter adapter after authenticated API access and permissions are available; synchronize claim metadata and documents into the existing tenant-aware data plane.
- **Phase 3:** consume App Events or an equivalent customer-approved webhook/event channel to trigger idempotent ingestion jobs.
- **Phase 4:** integrate identity and role mapping; map upstream roles/scopes to ClaimsRAG permissions.
- **Later:** consider explicitly approved state-changing commands such as invoice or claim actions. Keep these outside retrieval and require human confirmation, idempotency, audit, and rollback/compensation semantics.
