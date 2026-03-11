# zoompy Documentation

`zoompy` is a schema-driven Python SDK for the Zoom API. The documentation site
is organized so you can approach it from whichever direction matches your work:

- start with the guides if you are trying to learn how to use the SDK
- jump to the generated API reference if you already know the concepts and want
  signatures, docstrings, and runtime types
- use the mirrored project docs when you need release notes, contribution
  guidance, or the security policy

## What `zoompy` gives you

- a scripting-friendly SDK such as `client.users.get(user_id="me")`
- a validated low-level `request(...)` escape hatch
- Server-to-Server OAuth token acquisition and caching
- response validation against bundled OpenAPI documents
- runtime webhook payload validation
- retry and backoff logic
- structured logging
- schema sync tooling and extensive contract tests

## Suggested reading order

1. [Getting Started](guides/getting-started.md) for installation, configuration,
   and the first successful request.
2. [SDK Guide](guides/sdk-guide.md) for the dynamic SDK surface, typed models,
   request-body behavior, and pagination helpers.
3. [Validation and Schemas](guides/validation-and-schemas.md) for how response,
   webhook, and master-account schemas fit together.
4. [Logging and Runtime Behavior](guides/logging-and-runtime.md) for structured
   logs, retries, and lower-level `request(...)` behavior.
5. [API Reference](api/index.md) when you want generated class and method docs.

## Source documents

The project still treats the repository root markdown files as canonical
sources. This site mirrors them so GitHub browsing and published docs stay in
sync:

- [README](generated/readme.md)
- [Changelog](generated/changelog.md)
- [Contributing](generated/contributing.md)
- [Security](generated/security.md)
