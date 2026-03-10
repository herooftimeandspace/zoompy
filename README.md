# zoompy

`zoompy` is a production-ready Python library for the Zoom REST API. The
package provides a single unified client, handles Server-to-Server OAuth token
acquisition internally, validates responses against bundled OpenAPI schemas, and
ships with a large schema-driven contract test suite.

The repository is intentionally built around contract tests. Instead of manually
coding and hand-testing every endpoint family separately, the test suite derives
expected request and response behavior from Zoom's published schema documents.
The library implementation is designed to satisfy those contracts while still
remaining readable, typed, and suitable for real application code.

## Features

### Unified request interface

The public API is centered around one method:

```python
from zoompy import ZoomClient

client = ZoomClient()
result = client.request(
    "GET",
    "/users/{userId}",
    path_params={"userId": "me"},
)
```

The method handles:

- path parameter substitution
- authorization header injection
- request execution with `httpx`
- retry and backoff
- JSON parsing
- OpenAPI response validation

The same `request()` method supports both ordinary Zoom endpoints and
master-account endpoints. `zoompy` loads both path-based schema families and
selects the matching OpenAPI operation from the request path automatically.

### Layered SDK interface

`request()` remains the transport core, but `zoompy` now also builds a dynamic
SDK surface from the bundled OpenAPI operations. That gives script authors a
friendlier interface without duplicating the runtime logic:

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    users = client.users.list(page_size=10)
    me = client.users.get(user_id="me")
    phone_user = client.phone.users.get(user_id="abc123")
```

Those SDK calls still delegate to the same validated `request()` method
internally. In other words, the ergonomic layer is additive; it does not
replace the low-level client.

### Token caching and Server-to-Server OAuth

When you do not provide an explicit `access_token`, `zoompy` performs the Zoom
Server-to-Server OAuth account credentials flow automatically:

- `POST https://zoom.us/oauth/token`
- `grant_type=account_credentials`
- `account_id=<your account id>`
- HTTP Basic authentication using `client_id` and `client_secret`

Tokens are cached in memory and refreshed only when they are near expiry. A
threading lock prevents concurrent refresh storms in multi-threaded programs.

### Schema validation

The package bundles OpenAPI schema files under `src/zoompy/endpoints/**`. Every
successful JSON response is validated against the matching schema operation and
status code.

`src/zoompy/endpoints` is the canonical ordinary endpoint schema tree. The
repository also keeps a mirrored copy under `src/tests/endpoints` because the
existing contract tests load schema files directly by path.

Master-account OpenAPI documents are synced separately under
`src/zoompy/master_accounts` and mirrored to `src/tests/master_accounts`.
They remain outside the ordinary endpoint tree so the repository can keep a
clean one-to-one mirror of Zoom's product-family layout without colliding with
ordinary endpoint filenames.

Webhook OpenAPI documents are synced separately under `src/zoompy/webhooks`
and mirrored to `src/tests/webhooks`. They are stored outside the path-based
API trees because webhook specs use the OpenAPI `webhooks` section rather than
`paths`, so they should not be mixed into the client's response-validation
registry.

Use `scripts/sync_schemas.py` to refresh endpoint, master-account, and webhook
documents from a manually curated URL list and mirror them into the test tree
in one step.

Those webhook schemas now serve both the repository's contract suites and the
runtime webhook validator exposed by `ZoomClient.validate_webhook(...)`.

If the response body does not match the documented schema, `zoompy` raises
`ValueError` with a concise message that includes:

- HTTP method
- request path
- response status code
- a few validation error details

### Structured logging

Structured logging is implemented with the standard library `logging` module
only. Logging is opt-in, so the library does not configure handlers unless you
ask it to.

Enable logging like this:

```python
from zoompy import ZoomClient, configure_logging

configure_logging(level="INFO")
client = ZoomClient()
```

Logs are emitted as JSON and include fields such as:

- timestamp
- log level
- logger name
- message
- event name
- method
- URL
- path
- status code
- duration in milliseconds
- retry attempt
- request or trace identifiers when available

Secrets are intentionally never logged:

- `client_secret` is never logged
- Authorization headers are never logged
- raw bearer tokens are never logged

### Retry and backoff

Retries use the standard library only. The client retries:

- transport errors such as connection failures and timeouts
- HTTP `429`
- HTTP `500`, `502`, `503`, and `504`

Retries are not attempted for ordinary `4xx` responses except `429`.

Default retry settings:

- `max_retries=3`
- `backoff_base_seconds=0.5`
- `backoff_max_seconds=8.0`

Backoff uses exponential delay with jitter:

```text
sleep = min(backoff_max_seconds, backoff_base_seconds * (2 ** attempt))
sleep *= random.uniform(0.75, 1.25)
```

If Zoom returns `Retry-After` on `429`, the client prefers that value, capped to
the configured maximum.

## Architecture Overview

The package is intentionally split into a few small modules:

- `zoompy.client`
  The public `ZoomClient` implementation and request lifecycle.
- `zoompy.auth`
  Token acquisition and in-memory token caching.
- `zoompy.config`
  Environment loading and normalized settings assembly.
- `zoompy.schema`
  OpenAPI indexing, webhook lookup, `$ref` resolution, and payload validation.
- `zoompy.logging`
  JSON log formatting and opt-in logger configuration.

This separation matters because future maintainers can usually answer one
question by opening one module instead of tracing everything through the client.

## OAuth Configuration

The client reads the following environment variables:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`
- `ZOOM_BASE_URL` (default fallback: `https://api.zoom.us/v2`)
- `ZOOM_OAUTH_URL` (default: `https://zoom.us`)
- `ZOOM_TOKEN_SKEW_SECONDS` (default: `60`)

You may also pass these values directly to `ZoomClient(...)` as constructor
arguments. Explicit constructor values win over environment values.

`ZOOM_BASE_URL` is the client's fallback base URL. When the matched bundled
OpenAPI schema declares a more specific server URL for an endpoint family, the
client prefers that schema-declared server. This applies to both ordinary
endpoints and master-account endpoints. It matters for endpoint groups such as
Clips, SCIM, and file-upload APIs that do not consistently live under the
default `/v2` server URL.

If you already have a bearer token from another system, you can bypass OAuth:

```python
client = ZoomClient(access_token="your-preissued-token")
```

This is especially useful in tests and in environments with centralized secret
brokers.

## `.env` Support

For local development, `zoompy` supports a `.env` file without adding a dotenv
dependency.

Behavior:

- existing environment variables are never overwritten
- blank lines are ignored
- comment lines are ignored
- surrounding quotes are stripped
- the loader walks upward from the current working directory until it finds a
  directory containing `pyproject.toml`, then reads `.env` from there

Example:

```dotenv
ZOOM_ACCOUNT_ID="your_account_id"
ZOOM_CLIENT_ID="your_client_id"
ZOOM_CLIENT_SECRET="your_client_secret"
```

See [.env.example](./.env.example).

## Usage Examples

### Simple request

```python
from zoompy import ZoomClient

client = ZoomClient()

try:
    users = client.request("GET", "/users", params={"page_size": 30})
finally:
    client.close()
```

### SDK-style access

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    users = client.users.list(page_size=10)
    user = client.users.get(user_id="me")
    phone_user = client.phone.user.get(id="1234")
```

Generated SDK methods support a few conventions:

- path parameters accept snake_case names like `user_id`
- a generic `id=` alias works when a method has exactly one path parameter
- known query parameters remain query parameters
- leftover keyword arguments become JSON body fields for body-capable methods
- unusual operations are still available through snake-cased `operationId`
  methods when a simple CRUD alias would be unclear
- `.raw(...)` is available when you explicitly want plain JSON instead of typed
  objects

### Typed SDK access

The SDK layer now returns typed Pydantic model objects by default whenever a
representative success-response schema is available. In other words, normal SDK
calls already behave like a real typed scripting SDK:

Example:

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    user = client.users.get(user_id="me")
    print(user.display_name)

    created = client.users.create(
        email="person@example.com",
        first_name="Ada",
    )
    print(created.email)
```

The older low-level model hooks still exist for advanced use:

- `request_model`
- `response_model`
- `.typed(...)`

They are no longer the primary interface. They mainly exist as escape hatches
for advanced callers and internal testing. If you want plain validated JSON
instead of model objects, use `.raw(...)`.

### Context manager

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    meeting = client.request(
        "GET",
        "/meetings/{meetingId}",
        path_params={"meetingId": "123456789"},
    )
```

### Webhook validation

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    client.validate_webhook(
        "meeting.started",
        {
            "event": "meeting.started",
            "payload": {"object": {"id": "123456789"}},
        },
    )
```

When an event name is not unique enough on its own, narrow validation with
`schema_name=` or `operation_id=`.

### Tuned retry policy

```python
client = ZoomClient(
    max_retries=5,
    backoff_base_seconds=1.0,
    backoff_max_seconds=10.0,
)
```

### Structured logging

```python
from zoompy import ZoomClient, configure_logging

configure_logging(level="DEBUG")

with ZoomClient() as client:
    client.request("GET", "/users", params={"page_size": 10})
```

## Response Validation Details

For each successful JSON response, the client:

1. determines the matching schema document from the packaged schema registry
2. finds the operation by method and path
3. selects the documented response schema using:
   - exact status code when available
   - any `2xx` fallback
   - `default` fallback
4. resolves local `$ref` references
5. normalizes minor upstream schema irregularities, such as capitalized type
   names like `Integer`
6. validates the parsed JSON with `Draft202012Validator`

If no matching operation is found, the client raises `ValueError`. This is
intentional: schema validation is always on, so "unknown endpoint behavior"
should fail loudly rather than silently skipping validation.

## Error Handling

`zoompy` raises:

- `httpx.HTTPStatusError`
  for non-2xx responses after retry exhaustion
- `ValueError`
  for schema validation failures or invalid JSON response bodies

That split keeps transport/HTTP failures clearly separate from contract
violations.

## What zoompy does not do

The library is intentionally focused. At the moment it does not:

- verify Zoom webhook signatures for you
- generate checked-in, hand-maintained per-endpoint service classes
- download fresh schemas dynamically at runtime
- bypass schema validation when an operation is unknown

Webhook payload shape validation is supported through
`ZoomClient.validate_webhook(...)`, but request authenticity checks still belong
in the application that receives the webhook.

## Development

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

### Run static checks

```bash
ruff check .
mypy src
```

### Run tests

```bash
pytest -m "not integration"
```

### Run integration smoke test

The integration suite is intentionally minimal. It exists to prove that live
credentials can successfully acquire an OAuth token.

```bash
pytest -m integration
```

If the required Zoom credentials are missing, the integration test is skipped.

## Docker Usage

A simple Docker Compose workflow is included:

```bash
cp .env.example .env
docker compose up --build
```

The compose file:

- uses Python 3.14
- mounts the repository into the container
- loads configuration from `.env`
- installs dependencies
- runs `pytest`

## CI

The GitHub Actions workflow defines two jobs:

### `unit`

Runs on every push and pull request:

- checkout
- Python 3.14 setup
- dependency installation
- `ruff check`
- `mypy`
- `python -m build`
- `pytest -m "not integration"`

### `integration`

Runs after `unit` and expects repository secrets:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`
- optional URL overrides

It runs:

```bash
pytest -m integration
```

## Testing Strategy

This repository uses multiple layers of testing:

1. schema-driven contract tests under `src/tests`
   These verify request, response, webhook, and master-account behavior against
   bundled OpenAPI schemas.
2. client integration fixtures in `src/tests/conftest.py`
   These connect the contract tests to the real production client.
3. integration smoke test under `src/tests/integration`
   This verifies real token acquisition when credentials are available.

To refresh schemas from the URLs listed in `scripts/schema_urls.json` and then
mirror the canonical tree into the test tree, run:

```bash
./.venv/bin/python scripts/sync_schemas.py
```

Edit `scripts/schema_urls.json` first so it contains the exact endpoint schema
JSON URLs you want to trust and download. For each endpoint URL, the sync script
also derives the companion `events/webhooks.json` and `ma/master.json` URLs
automatically.

The sync script matches each downloaded schema to a local file by the schema's
`info.title`, not by the remote URL basename, so URLs like
`.../meetings/methods/endpoints.json` still update `Meetings.json`. You can
also provide `expected_title` in the manifest to make that mapping explicit.
If a webhook or master-account document uses a different title than its
ordinary endpoint schema, provide `webhook_expected_title` or
`master_account_expected_title` in the manifest. Derived webhook and
master-account URLs that return `404` are treated as optional and do not fail
the whole sync.

To only rebuild the test mirror from the canonical package endpoint,
master-account, and webhook trees, run:

```bash
./.venv/bin/python scripts/sync_schemas.py --mirror-only
```

The contract tests are the main source of behavioral confidence. The
integration smoke test exists to confirm that the live OAuth path still works.
