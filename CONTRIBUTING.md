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

- ordinary endpoint documents live under `src/zoompy/endpoints`
- master-account documents live under `src/zoompy/master_accounts`
- webhook documents live under `src/zoompy/webhooks`

Do not edit generated JSON files by hand unless you are intentionally fixing a
local sync problem. Update `scripts/schema_urls.json` and run:

```bash
./.venv/bin/python scripts/sync_schemas.py
```

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

`zoompy` now has a public SDK surface on top of the lower-level request client.
When contributing, treat these SDK behaviors as user-facing API:

- namespace layout such as `client.users` and `client.phone.users`
- snake_case method parameters derived from schema parameters
- normal typed return behavior for SDK calls
- `.raw(...)`
- pagination helpers like `iter_pages(...)`, `iter_all(...)`, and `paginate(...)`
- do not introduce generic parameter aliases that are not present in the schema

If you need to rename or remove a public SDK method, document the change in
[CHANGELOG.md](./CHANGELOG.md) and treat it as a breaking change.
