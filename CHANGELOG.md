# Changelog

All notable changes to `zoom-sdk-python` should be documented in this file.

The project follows a keep-a-changelog style format with a lightweight
semantic-versioning policy for the public SDK surface.

## [Unreleased]

### Changed
- CI now runs integration tests only for `staging` and `main`, while `dev` and
  ordinary feature work stay on the faster unit-quality path.
- Documentation publishing now runs from a dedicated `workflow_run` pipeline
  that only deploys GitHub Pages after a successful `main` CI run.
- Repository documentation now explains the intended promotion path:
  `dev -> staging -> main`.

## [1.0.1]

### Added
- A MkDocs Material documentation suite that combines narrative guides, the
  canonical project markdown files, and generated API reference output from
  `pdoc`.
- A dedicated docs build script, `scripts/build_docs.py`, to assemble the
  published documentation tree from the repository source files.

### Changed
- GitHub Actions CI now validates the full documentation site build with MkDocs
  Material instead of only generating raw `pdoc` HTML.
- GitHub Pages now publishes the integrated documentation site rather than a
  standalone API-reference artifact.
- Local documentation workflows now use the MkDocs build and serve commands.

## [1.0.0]

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
- Schema-derived SDK signatures and richer generated method docstrings so
  editors, `help(...)`, and published API docs expose expected parameter types,
  request-body hints, and return-model guidance.
- Focused SDK tests that reject malformed typed response payloads before they
  can be exposed as model objects.

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
- Structured logging now defaults the `zoom_sdk` package logger to `INFO` while
  still leaving handler configuration to the consuming application.
- The schema sync utility now routes progress and warning output through the
  standard logging system instead of `print(...)`.
- CI now validates docs generation in addition to linting, typing, packaging,
  and tests on every push and pull request.
- The published documentation now reflects the dynamic SDK as the primary
  scripting interface rather than the older low-level typed-model hooks.
- The SDK documentation now emphasizes how to learn valid request shapes from
  signatures, generated docstrings, `request_model`, and `response_model`.

### Fixed
- Request base URL selection now respects schema-declared server URLs for
  endpoint families that do not live under the default `/v2` base.
- Runtime and test-side schema validation are more resilient to real Zoom
  schema irregularities such as malformed type names, conflicting examples,
  optional empty-string enum fields, and composed schemas.
- The configured `mypy` quality gate is now green for the tracked source tree
  and shared OpenAPI contract helper.

### Removed
- Redundant `SdkMethod.typed(...)` alias. Normal SDK calls already return typed
  models by default, so the extra wrapper is no longer part of the public SDK
  surface.

## [0.1.0]

### Added
- Initial unified Zoom client with:
  - Server-to-Server OAuth
  - schema-driven response validation
  - structured logging
  - retry and backoff
  - contract-test-driven repository structure
