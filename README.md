# zoom-sdk-python

[![Build (main)](https://img.shields.io/github/actions/workflow/status/herooftimeandspace/zoom-sdk-python/ci.yml?branch=main&label=build%20(main))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Amain)
[![Build (staging)](https://img.shields.io/github/actions/workflow/status/herooftimeandspace/zoom-sdk-python/ci.yml?branch=staging&label=build%20(staging))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Astaging)
[![Build (dev)](https://img.shields.io/github/actions/workflow/status/herooftimeandspace/zoom-sdk-python/ci.yml?branch=dev&label=build%20(dev))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Adev)
[![Coverage (main)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/herooftimeandspace/zoom-sdk-python/main/badges/coverage.json&label=coverage%20(main))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Amain)
[![Coverage (staging)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/herooftimeandspace/zoom-sdk-python/staging/badges/coverage.json&label=coverage%20(staging))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Astaging)
[![Coverage (dev)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/herooftimeandspace/zoom-sdk-python/dev/badges/coverage.json&label=coverage%20(dev))](https://github.com/herooftimeandspace/zoom-sdk-python/actions/workflows/ci.yml?query=branch%3Adev)

`zoom-sdk-python` is a production-ready Python SDK for the Zoom REST API. The
published package name is `zoom-sdk-python`, and the Python import package is
`zoom_sdk`.

> AI assistance disclosure
>
> This repository was developed with substantial assistance from AI tooling,
> including ChatGPT. That disclosure is intentional and should remain part of
> the project documentation so future maintainers and users understand how the
> codebase was produced and reviewed.

It combines:

- a schema-driven scripting SDK such as `client.users.get(...)`
- a lower-level validated `request(...)` escape hatch
- Server-to-Server OAuth token acquisition and caching
- bundled OpenAPI validation for responses and webhooks
- retry and backoff logic
- structured logging
- a large schema-driven contract suite

The repository is intentionally built around contract tests. Instead of manually
coding and hand-testing every endpoint family separately, the test suite derives
expected request and response behavior from Zoom's published schema documents.
The library implementation is designed to satisfy those contracts while still
remaining readable, typed, and suitable for real application code.

## Features

### SDK-first interface

The normal way to use `zoom_sdk` is through the generated SDK surface:

```python
from zoom_sdk import ZoomClient

with ZoomClient() as client:
    me = client.users.get(user_id="me")
    phone_user = client.phone.users.get(user_id="abc123")
    meetings = client.meetings.meeting_summaries.list(page_size=10)
```

Normal SDK calls:

- use schema-derived snake_case parameter names
- return typed Pydantic model objects when representative response schemas
  exist
- expose `.raw(...)` when you explicitly want validated JSON instead
- expose pagination helpers such as `iter_pages(...)`, `iter_all(...)`, and
  `paginate(...)`
- expose schema-derived signatures and rich docstrings so editor hover,
  `help(...)`, and generated API docs show expected parameter and return types

Under the hood, every SDK call still delegates to the same validated request
core, so the ergonomic layer does not bypass retries, logging, auth, or schema
validation.

### Unified request interface

The lower-level runtime core is still available when you want direct control
over method and path handling:

```python
from zoom_sdk import ZoomClient

with ZoomClient() as client:
    result = client.request(
        "GET",
        "/users/{userId}",
        path_params={"userId": "me"},
    )
```

That lower-level interface handles:

- path parameter substitution
- authorization header injection
- request execution with `httpx`
- retry and backoff
- JSON parsing
- OpenAPI response validation

The same `request()` method supports both ordinary Zoom endpoints and
master-account endpoints. `zoom_sdk` loads both path-based schema families and
selects the matching OpenAPI operation from the request path automatically.

### SDK stability

The public SDK surface is intended to be stable for outside consumers.

Stable SDK behaviors:

- namespace access such as `client.users`, `client.phone.users`,
  `client.meetings`
- snake_case parameter names derived from the schema
- typed model returns for normal SDK calls when a representative response model
  exists
- `.raw(...)` for plain validated JSON
- pagination helpers:
  - `iter_pages(...)`
  - `iter_all(...)`
  - `paginate(...)`

Compatibility policy:

- additive namespaces and methods are allowed in minor releases
- renaming or removing an existing public SDK method is a breaking change
- schema syncs may add new methods as Zoom expands the API surface
- generic parameter aliases that are not present in the schema are not part of
  the SDK contract
- breaking SDK changes should be called out explicitly in
  [CHANGELOG.md](./CHANGELOG.md)
- package releases expose a version string at `zoom_sdk.__version__`

### Token caching and Server-to-Server OAuth

When you do not provide an explicit `access_token`, `zoom_sdk` performs the Zoom
Server-to-Server OAuth account credentials flow automatically:

- `POST https://zoom.us/oauth/token`
- `grant_type=account_credentials`
- `account_id=<your account id>`
- HTTP Basic authentication using `client_id` and `client_secret`

Tokens are cached in memory and refreshed only when they are near expiry. A
threading lock prevents concurrent refresh storms in multi-threaded programs.

### Schema validation

The package bundles OpenAPI schema files under `src/zoom_sdk/endpoints/**`. Every
successful JSON response is validated against the matching schema operation and
status code.

`src/zoom_sdk/endpoints` is the canonical ordinary endpoint schema tree. The
repository also keeps a mirrored copy under `src/tests/endpoints` because the
existing contract tests load schema files directly by path.

Master-account OpenAPI documents are synced separately under
`src/zoom_sdk/master_accounts` and mirrored to `src/tests/master_accounts`.
They remain outside the ordinary endpoint tree so the repository can keep a
clean one-to-one mirror of Zoom's product-family layout without colliding with
ordinary endpoint filenames.

Webhook OpenAPI documents are synced separately under `src/zoom_sdk/webhooks`
and mirrored to `src/tests/webhooks`. They are stored outside the path-based
API trees because webhook specs use the OpenAPI `webhooks` section rather than
`paths`, so they should not be mixed into the client's response-validation
registry.

Use `scripts/sync_schemas.py` to refresh endpoint, master-account, and webhook
documents from a manually curated URL list and mirror them into the test tree
in one step.

Those webhook schemas now serve both the repository's contract suites and the
runtime webhook validator exposed by `ZoomClient.validate_webhook(...)`.

If the response body does not match the documented schema, `zoom_sdk` raises
`ValueError` with a concise message that includes:

- HTTP method
- request path
- response status code
- a few validation error details

### Structured logging

Structured logging is implemented with the standard library `logging` module
only. The `zoom_sdk` logger defaults to the `INFO` level, but the library still
does not choose an output destination for you. Applications remain responsible
for attaching console, file, or other logging handlers.

If you want `zoom_sdk` to emit its built-in JSON logs to stderr, enable it like
this:

```python
from zoom_sdk import ZoomClient, configure_logging

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

- `zoom_sdk.client`
  The public `ZoomClient` implementation and request lifecycle.
- `zoom_sdk.auth`
  Token acquisition and in-memory token caching.
- `zoom_sdk.config`
  Environment loading and normalized settings assembly.
- `zoom_sdk.schema`
  OpenAPI indexing, webhook lookup, `$ref` resolution, and payload validation.
- `zoom_sdk.logging`
  JSON log formatting and logger configuration helpers.

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

For local development, `zoom_sdk` supports a `.env` file without adding a dotenv
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
from zoom_sdk import ZoomClient

client = ZoomClient()

try:
    users = client.request("GET", "/users", params={"page_size": 30})
finally:
    client.close()
```

### SDK-style access

```python
from zoom_sdk import ZoomClient

with ZoomClient() as client:
    users = client.users.list(page_size=10)
    user = client.users.get(user_id="me")
    phone_user = client.phone.users.get(user_id="abc123")
    updated = client.phone.users.update_profile(
        user_id="abc123",
        display_name="Ada Lovelace",
    )
```

Generated SDK methods support a few conventions:

- path parameters accept snake_case names like `user_id`
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
import logging

from zoom_sdk import ZoomClient

logger = logging.getLogger(__name__)

with ZoomClient() as client:
    user = client.users.get(user_id="me")
    logger.info("Loaded user %s", user.display_name)

    created = client.users.create(
        email="person@example.com",
        first_name="Ada",
    )
    logger.info("Created user %s", created.email)
```

Most callers do not need to think about model plumbing at all. The common
pattern is simply:

- call the SDK method with schema-derived snake_case parameters
- work with the returned typed model object
- use `.raw(...)` only when you explicitly want validated JSON

### Learning request shapes from tooling

The SDK is designed to teach you how to call it while you work.

Generated SDK methods expose:

- a schema-derived Python signature
- path and query parameter names in snake_case
- request body hints when a JSON body is accepted
- typed response-model names when a representative response schema exists

That means editor hover text, `inspect.signature(...)`, and `help(...)` can
often answer "what arguments does this method need?" without sending you back
to the raw OpenAPI files.

Example:

```python
import inspect
import logging

from zoom_sdk import ZoomClient

logger = logging.getLogger(__name__)

with ZoomClient() as client:
    logger.info("%s", inspect.signature(client.phone.users.get))
    help(client.users.create)
```

When a method accepts a JSON request body, there are two useful ways to learn
the expected shape:

1. Read the generated method docstring.
2. Inspect the generated request model.

Example:

```python
import logging

from zoom_sdk import ZoomClient

logger = logging.getLogger(__name__)

with ZoomClient() as client:
    model = client.users.create.request_model
    if model is not None:
        logger.info("%s", list(model.model_fields))
```

The normal scripting path is still intentionally simple:

- pass schema-derived snake_case path and query parameters directly
- for body-capable methods, either:
  - pass `body=<typed model or dict>`
  - or pass leftover keyword arguments and let the SDK build the JSON body
- let the returned typed model guide downstream access

The lower-level model hooks still exist for advanced use and introspection:

- `request_model`
- `response_model`

They are no longer the primary interface. They mainly exist for advanced
callers, tooling, and internal tests. If you want plain validated JSON instead
of model objects, use `.raw(...)`.

### Pagination helpers

Most Zoom list endpoints use `next_page_token`. The SDK exposes that directly:

```python
import logging

from zoom_sdk import ZoomClient

logger = logging.getLogger(__name__)

with ZoomClient() as client:
    for page in client.users.list.paginate(page_size=100):
        logger.info(
            "page token=%s total_records=%s",
            page.next_page_token,
            page.total_records,
        )
        for user in page.items:
            logger.info("user_id=%s", user.user_id)

    for user in client.users.list.iter_all(page_size=100):
        logger.info("user_id=%s", user.user_id)
```

### Context manager

```python
from zoom_sdk import ZoomClient

with ZoomClient() as client:
    meeting = client.request(
        "GET",
        "/meetings/{meetingId}",
        path_params={"meetingId": "123456789"},
    )
```

### Webhook validation

```python
from zoom_sdk import ZoomClient

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
from zoom_sdk import ZoomClient, configure_logging

configure_logging(level="DEBUG")

with ZoomClient() as client:
    client.request("GET", "/users", params={"page_size": 10})
```

## API Documentation

The repository now publishes a single documentation site built with
`mkdocs-material`.

That site combines:

- narrative guides under `docs/`
- generated copies of the root project documents:
  - `README.md`
  - `CHANGELOG.md`
  - `CONTRIBUTING.md`
- `SECURITY.md`
- generated API reference HTML from `pdoc`

Documentation is rebuilt automatically in GitHub Actions whenever
documentation-facing sources change, including:

- files under `docs/`
- `README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, and `SECURITY.md`
- `mkdocs.yml`
- `scripts/build_docs.py`
- Python modules under `src/zoom_sdk/`, so docstring changes rebuild the API
  reference too

The main CI workflow also computes test coverage with `pytest-cov` and writes a
Shields-compatible badge payload to `badges/coverage.json` on branch pushes.
That is what drives the branch-specific coverage badges at the top of this
README.

GitHub Pages publication is intentionally stricter than docs validation:

- CI validates the docs site on pushes and pull requests
- the dedicated Pages publishing workflow only runs after a successful `CI`
  workflow on `main`
- docs are not published from `dev` or `staging`

Build the docs locally with:

```bash
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs build --strict
```

For local preview with live reload:

```bash
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs serve
```

The built site is written to `site/`. Open `site/index.html` or use the MkDocs
development server to inspect the documentation locally.

The generated docs are useful for two audiences:

- script authors who want to browse the public `ZoomClient` and SDK surface
- maintainers who want the published docs to stay aligned with the actual code
  and root project documents

Because the docs come from the installed package, repository root markdown
files, and generated API pages, keeping docstrings and project documents
accurate is part of the project's public API discipline.

## Recommended Branch Promotion Model

The repository is designed to work best with a three-branch promotion chain:

- `dev` is the default integration branch and the normal base for feature work
- `staging` receives promotion PRs from `dev`
- `main` receives promotion PRs from `staging`

The CI workflows are configured so that:

- pushes and pull requests always run the unit-quality gate
- integration tests only run for `staging` and `main`, or for pull requests
  targeting those branches
- GitHub Pages only publishes after `main` CI succeeds

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

`zoom_sdk` raises:

- `httpx.HTTPStatusError`
  for non-2xx responses after retry exhaustion
- `ValueError`
  for schema validation failures or invalid JSON response bodies

That split keeps transport/HTTP failures clearly separate from contract
violations.

## What zoom_sdk does not do

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
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip install -e .
```

### Run static checks

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy src _openapi_contract.py
```

### Run tests

```bash
./.venv/bin/python -m pytest -m "not integration"
```

### Run integration smoke test

The integration suite is intentionally minimal. It exists to prove that live
credentials can successfully acquire an OAuth token.

```bash
./.venv/bin/python -m pytest -m integration
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

GitHub Actions runs the main quality gates on every push and every pull
request:

- `ruff check .`
- `mypy src _openapi_contract.py`
- `python -m build`
- `pytest -m "not integration"`
- documentation-site assembly with `scripts/build_docs.py`
- `mkdocs build --strict`

That means normal pushes already verify that tests, static analysis, packaging,
and documentation generation continue to work together.

The live integration workflow still runs separately because it requires Zoom
credentials from GitHub Secrets.

## GitHub Pages

The full documentation site is also published with GitHub Actions to GitHub
Pages.

The Pages workflow:

- installs the package and dev dependencies
- assembles the docs tree with `scripts/build_docs.py`
- builds the MkDocs Material site
- uploads the generated `site/` directory
- deploys that artifact to GitHub Pages on pushes to `main`

After GitHub Pages is enabled for the repository, the published site becomes the
main way to browse the SDK guides, API reference, changelog, contribution
guidance, and security policy without reading the source tree directly.

The GitHub Actions workflow defines two jobs:

### `unit`

Runs on every push and pull request:

- checkout
- Python 3.14 setup
- dependency installation
- `ruff check`
- `mypy src _openapi_contract.py`
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
