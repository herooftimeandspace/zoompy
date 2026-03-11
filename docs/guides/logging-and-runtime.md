# Logging and Runtime Behavior

This guide covers the lower-level runtime concerns that still matter even when
you mostly use the higher-level SDK surface.

## Structured logging

`zoompy` uses the standard library `logging` module only.

Important behavior:

- the `zoompy` logger defaults to `INFO`
- the library does **not** decide where logs are written
- applications remain responsible for attaching console, file, or other
  handlers

To enable the built-in JSON log formatter:

```python
from zoompy import ZoomClient, configure_logging

configure_logging("INFO")

with ZoomClient() as client:
    client.users.get(user_id="me")
```

The logs include request lifecycle details such as:

- method
- URL
- path
- status code
- duration
- retry attempt

Secrets are intentionally excluded from log output.

## Retry behavior

The client retries:

- transport exceptions such as connection failures and timeouts
- HTTP `429`
- HTTP `500`, `502`, `503`, and `504`

It does **not** retry ordinary `4xx` responses other than `429`.

The retry loop uses exponential backoff with jitter and respects
`Retry-After` on `429` when present.

## Lower-level request API

The SDK layer eventually delegates to:

```python
client.request(...)
```

That method is still the best tool when you need:

- exact control over the HTTP method
- direct control over the path template
- access to a newly synced endpoint before you have learned its SDK alias
- a stable escape hatch for unusual operations

## Webhook validation

The runtime client now exposes:

```python
client.validate_webhook(...)
```

That validates webhook payload shapes against the bundled webhook schemas.

It does **not** currently verify Zoom webhook signatures for you. Signature and
authenticity checks still belong in your application’s inbound request layer.
