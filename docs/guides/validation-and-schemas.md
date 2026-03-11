# Validation and Schemas

`zoompy` is built around bundled OpenAPI documents. They serve three different
jobs in the repository:

## Endpoint schemas

Ordinary outbound API documents live under:

- `src/zoompy/endpoints`

The runtime client uses them to:

- match request paths to documented operations
- choose schema-declared server URLs when needed
- validate successful JSON responses
- build the dynamic SDK namespaces and methods

## Master-account schemas

Master-account API documents live under:

- `src/zoompy/master_accounts`

They are loaded into the same path-based registry as ordinary endpoints, which
means `ZoomClient.request(...)` and the dynamic SDK can use them transparently.

## Webhook schemas

Webhook documents live under:

- `src/zoompy/webhooks`

They do not belong in the path-based request registry because they describe
incoming webhook payloads, not outbound REST requests. The runtime webhook
validator uses them through `ZoomClient.validate_webhook(...)` and
`WebhookRegistry`.

## Schema sync

The repository does not fetch schemas dynamically at runtime. Instead, it keeps
vendored copies and refreshes them through a batch sync step:

```bash
./.venv/bin/python scripts/sync_schemas.py
```

That script:

- downloads endpoint schemas from a curated manifest
- downloads optional webhook and master-account companions
- updates the canonical runtime trees
- mirrors them into the test trees used by the contract suites

## Validation behavior

The client validates:

- successful JSON responses from outbound API requests
- incoming webhook payloads when you call the webhook validator

If a payload does not match the documented schema, `zoompy` raises
`ValueError`.

The runtime also includes a small amount of schema normalization so it can
tolerate known irregularities in Zoom’s published OpenAPI documents, such as:

- malformed type names like `Integer`
- composed schemas with sparse `required` declarations
- optional enum-backed string fields returned as empty strings in live traffic

These compatibility rules are intentionally narrow. They exist to keep the
client practical against real Zoom responses without turning validation into a
no-op.
