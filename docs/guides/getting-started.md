# Getting Started

This guide is for the fastest path from "I installed the package" to
"I made a valid request."

## Installation

Install the package and its runtime dependencies:

```bash
./.venv/bin/python -m pip install -e .
```

For contributor and documentation work, install the development dependencies
from the repository requirements file instead:

```bash
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m pip install -e .
```

## Required Zoom credentials

`zoompy` uses Server-to-Server OAuth unless you provide an explicit bearer
token.

Required environment variables:

- `ZOOM_ACCOUNT_ID`
- `ZOOM_CLIENT_ID`
- `ZOOM_CLIENT_SECRET`

Optional environment variables:

- `ZOOM_BASE_URL`
- `ZOOM_OAUTH_URL`
- `ZOOM_TOKEN_SKEW_SECONDS`

You can also provide these values directly to `ZoomClient(...)`.

## Minimal SDK example

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    me = client.users.get(user_id="me")
    print(me.display_name)
```

## Minimal low-level example

If you need exact control over method and path handling, the lower-level
request API is still available:

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    payload = client.request(
        "GET",
        "/users/{userId}",
        path_params={"userId": "me"},
    )
```

## Discovering valid parameters

The SDK is designed to help you build valid requests from tooling:

- `inspect.signature(client.phone.users.get)`
- `help(client.users.create)`
- `client.users.create.request_model`
- `client.users.get.response_model`

That means you can often learn the required snake_case parameter names and
payload expectations without reading the raw OpenAPI documents directly.

## First production smoke check

If you want a quick real-account read-only check, the repository includes:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/list_users.py
```

That script authenticates, fetches users through the SDK, and logs a simple
table for the first ten users returned by `/users`.
