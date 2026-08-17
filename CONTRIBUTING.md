# Contributing

This repository uses protected integration branches and automated promotion
pull requests. Contributors normally work only with feature branches and
`dev`; maintainers promote integrated commits through `staging` and `main`.

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
./.venv/bin/python -m mypy src _openapi_contract.py scripts/release_tools.py
./.venv/bin/python -m build
./.venv/bin/python -m pip_audit --cache-dir .cache/pip-audit
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs build --strict
```

For local docs preview:

```bash
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs serve
```

## Branch and pull-request flow

The durable branch chain is `dev -> staging -> main`:

1. Create feature, bug-fix, documentation, and maintenance branches from the
   latest `dev` commit.
2. Open the work pull request against `dev` and apply exactly one semantic
   version label.
3. Merge into `dev` only after `unit` and `security` pass.
4. A successful CI push run on `dev` creates or refreshes the `dev -> staging`
   promotion pull request and reports the live `integration` check on the exact
   dev head.
5. Merge the promotion pull request into `staging` only after `unit`,
   `security`, and `integration` pass.
6. A successful CI push run on `staging` creates or refreshes
   `promote/staging-to-main -> main`.
7. The promotion workflow builds a merge head containing the current `main`
   and `staging` tips, calculates the next version, updates `pyproject.toml` and
   `src/zoom_sdk/__init__.py`, and reports `unit`, `security`, `integration`,
   and `release-prep` on that exact commit.
8. Merge into `main` only after all four checks pass. The merge creates the
   matching semantic-version tag and a GitHub Release containing the Python
   wheel and source distribution.

The workflows use the repository `GITHUB_TOKEN`. They do not require a
personal access token. GitHub suppresses some recursive workflow events for
pull requests created by `GITHUB_TOKEN`, so the promotion workflow explicitly
reports the checks required on its prepared heads.

Direct pushes, force pushes, and deletion should be disabled on `dev`,
`staging`, and `main`. Normal work always uses a pull request.

## Quality gates

Run the complete deterministic local gate before opening a pull request:

```bash
./.venv/bin/python -m ruff check .
./.venv/bin/python -m mypy src _openapi_contract.py scripts/release_tools.py
./.venv/bin/python -m build
./.venv/bin/python scripts/build_docs.py
./.venv/bin/python -m mkdocs build --strict
./.venv/bin/python -m pytest -m "not integration" \
  --cov=zoom_sdk \
  --cov-report=term \
  --cov-fail-under=95
./.venv/bin/python -m pip_audit --cache-dir .cache/pip-audit
```

Run `./.venv/bin/python -m pytest -m integration` only when the documented Zoom
credentials are available and the task requires a live smoke test. Production
must not be the first place a new write path is exercised.

The `unit` gate owns linting, typing, package builds, documentation, unit and
contract tests, and the 95% coverage floor. The `security` gate owns dependency
auditing. The `integration` gate owns live read-only Zoom smoke tests.

Promotion jobs scope Zoom credential secrets to the live `integration` step.
The promotion-owned `unit`, `security`, and `release-prep` checks must remain
credential-free. When optional integration settings such as `ZOOM_BASE_URL`,
`ZOOM_OAUTH_URL`, or `ZOOM_TOKEN_SKEW_SECONDS` are not configured as secrets,
the workflow removes their empty GitHub Actions placeholders so the SDK's
documented defaults remain active.

Do not lower the coverage floor, weaken schema validation, or modify tests only
to silence a failure. Update behavior, tests, documentation, and generated
contracts together when intended behavior changes.

## Semantic-version labels

Every source and promotion pull request carries exactly one release-impact
label:

- `semver:patch` for compatible fixes, documentation, internal maintenance, or
  behavior-preserving refactors.
- `semver:minor` for backward-compatible additions to the public Python API.
- `semver:major` for breaking changes to public names, signatures, return
  behavior, configuration, or supported contracts.

The `dev -> staging` automation copies the label from the source pull request
associated with the promoted commit. The `staging -> main` automation examines
the complete unpromoted `main..staging` commit range, deduplicates associated
source pull requests, and selects the highest impact using
`major > minor > patch`. A missing label defaults safely to patch. Multiple
distinct semver labels on one source pull request fail closed.

Python package versions have two tracked declarations:

- `[project].version` in `pyproject.toml`
- `zoom_sdk.__version__` in `src/zoom_sdk/__init__.py`

Do not edit those declarations by hand during ordinary feature work. The
staging-to-main promotion workflow updates both together on the prepared branch
and `release-prep` verifies that they match the calculated version.

After the prepared promotion merges, the release workflow verifies that the
merge came from `promote/staging-to-main`, reads the prepared package version,
runs the non-integration test suite, builds wheel and source artifacts, and
creates the matching tag and GitHub Release. Non-promotion main merges do not
publish releases.

## Maintainer bootstrap and repository settings

Apply repository settings only after the promotion and release workflows exist
on the default branch:

- set `dev` as the default branch;
- grant Actions read/write workflow permissions and allow Actions to create
  pull requests;
- enable automatic deletion of merged branches;
- enable merge commits so promotion ancestry remains explicit;
- create `semver:patch`, `semver:minor`, and `semver:major` labels;
- protect `dev`, `staging`, and `main` with strict required checks, required
  pull requests, and force-push/deletion protection.

Branch-specific required checks are:

| Branch | Required checks |
| --- | --- |
| `dev` | `unit`, `security` |
| `staging` | `unit`, `security`, `integration` |
| `main` | `unit`, `security`, `integration`, `release-prep` |

The staging PR reuses the authoritative unit and security checks on the exact
dev commit and receives its integration check from the promotion workflow. The
main PR uses all checks reported by the promotion workflow on the prepared
versioned merge commit. Never satisfy branch protection with checks from an
older source or promotion head.

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
- apply exactly one semantic-version label

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
