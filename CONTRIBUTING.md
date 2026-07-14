# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
```

Use the repository virtual environment for validation commands:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy src
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs build --strict
```

For local docs preview:

```bash
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs serve
```

## Schema workflow

The repository treats bundled OpenAPI JSON as source code.

- ordinary endpoint documents live under `src/zoom_sdk/endpoints`
- master-account documents live under `src/zoom_sdk/master_accounts`
- webhook documents live under `src/zoom_sdk/webhooks`

Do not edit generated JSON files by hand unless you are intentionally fixing a
local sync problem. Update `scripts/schema_urls.json` and run:

```bash
./.venv/bin/python scripts/sync_schemas.py
```

## Language-specific configuration boundary

Functional parity across SDK implementations means matching observable API,
transport, validation, pagination, security, and release behavior. It does not
mean copying environment-variable names or build controls from another
language.

The complete Python runtime environment contract is declared by
`SUPPORTED_RUNTIME_ENVIRONMENT_VARIABLES` in `zoom_sdk.config` and mirrored in
`.env.example`. Add a runtime variable only when Python application code must
read it. Prefer an explicit command-line argument for repository maintenance
tools, and prefer existing `ZoomClient` constructor arguments or
`ZoomSettings` fields when they already express the behavior.

Do not add compiler settings, module-cache settings, foreign source-checkout
locations, or another SDK's parity controls to Python runtime code, examples,
or workflows. When a cross-language change arrives, write down the behavior to
preserve first and then implement the Pythonic equivalent, which may require no
new configuration.

## Tests

The important test layers are:

- generic endpoint contract tests
- generic master-account contract tests
- generic webhook contract tests
- runtime webhook validation tests
- focused runtime/client behavior tests
- live integration smoke tests

If you change the schema runtime, retry logic, or sync script, add focused unit
tests in addition to keeping the broad contract suites green.

## Pull requests

- keep changes typed
- keep docstrings and comments updated with behavior changes
- prefer small, reviewable commits
- do not add dependencies unless there is a clear justification

## SDK stability policy

`zoom-sdk-python` now has a public SDK surface on top of the lower-level
request client. The runtime import package remains `zoom_sdk`.
When contributing, treat these SDK behaviors as user-facing API:

- namespace layout such as `client.users` and `client.phone.users`
- snake_case method parameters derived from schema parameters
- normal typed return behavior for SDK calls
- `.raw(...)`
- pagination helpers like `iter_pages(...)`, `iter_all(...)`, and `paginate(...)`
- do not introduce generic parameter aliases that are not present in the schema

If you need to rename or remove a public SDK method, document the change in
[CHANGELOG.md](./CHANGELOG.md) and treat it as a breaking change.
