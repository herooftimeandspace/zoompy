# Changelog

All notable changes to `zoompy` should be documented in this file.

The project follows a keep-a-changelog style format with a lightweight
semantic-versioning policy for the public SDK surface.

## [Unreleased]

### Added
- Dynamic schema-driven SDK namespaces on `ZoomClient`, such as
  `client.users.get(...)` and `client.phone.users.get(...)`.
- Typed SDK return models by default for operations with representative success
  schemas.
- Explicit `.raw(...)` escape hatch for callers who want validated JSON
  instead of typed model objects.
- Pagination helpers:
  - `iter_pages(...)`
  - `iter_all(...)`
  - `paginate(...)`
- Runtime webhook validation through `ZoomClient.validate_webhook(...)`.
- Schema sync support for endpoint, webhook, and master-account documents.
- Exhaustive golden SDK coverage for the generated public surface, including a
  checked-in full inventory of generated SDK methods.
- Generic contract suites for endpoint, webhook, and master-account OpenAPI
  families.
- Production smoke scripts under `scripts/`, including a user-listing example
  that exercises the generated SDK against a live account.
- Static API documentation generation with `pdoc`.
- GitHub Pages publishing for the generated API reference.

### Changed
- SDK methods now use schema-derived snake_case parameter names instead of
  generic shorthands.
- SDK request-body methods treat leftover keyword arguments as JSON body fields
  by default when the operation accepts a request body.
- Normal SDK calls behave like a scripting SDK first: they return typed models
  when representative response schemas exist, while `.raw(...)` remains the
  explicit escape hatch for validated JSON.
- SDK alias generation and pagination helpers were refined to make awkward Zoom
  endpoint families feel more intentional while preserving schema-derived
  behavior.
- Structured logging now defaults the `zoompy` package logger to `INFO` while
  still leaving handler configuration to the consuming application.
- The schema sync utility now routes progress and warning output through the
  standard logging system instead of `print(...)`.
- CI now validates docs generation in addition to linting, typing, packaging,
  and tests on every push and pull request.
- The published documentation now reflects the dynamic SDK as the primary
  scripting interface rather than the older low-level typed-model hooks.

### Fixed
- Request base URL selection now respects schema-declared server URLs for
  endpoint families that do not live under the default `/v2` base.
- Runtime and test-side schema validation are more resilient to real Zoom
  schema irregularities such as malformed type names, conflicting examples,
  optional empty-string enum fields, and composed schemas.
- The configured `mypy` quality gate is now green for the tracked source tree
  and shared OpenAPI contract helper.

## [0.1.0]

### Added
- Initial unified Zoom client with:
  - Server-to-Server OAuth
  - schema-driven response validation
  - structured logging
  - retry and backoff
  - contract-test-driven repository structure
