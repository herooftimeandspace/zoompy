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

### Changed
- SDK methods now use schema-derived snake_case parameter names instead of
  generic shorthands.
- SDK request-body methods treat leftover keyword arguments as JSON body fields
  by default when the operation accepts a request body.

## [0.1.0]

### Added
- Initial unified Zoom client with:
  - Server-to-Server OAuth
  - schema-driven response validation
  - structured logging
  - retry and backoff
  - contract-test-driven repository structure
