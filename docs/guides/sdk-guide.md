# SDK Guide

The dynamic SDK layer is the primary interface for normal application and
automation work.

## Basic shape

```python
from zoompy import ZoomClient

with ZoomClient() as client:
    users = client.users.list(page_size=10)
    user = client.users.get(user_id="me")
    phone_user = client.phone.users.get(user_id="abc123")
```

The SDK is generated from the bundled OpenAPI documents, so its public method
surface stays grounded in the Zoom schema corpus rather than hand-maintained
service wrappers.

## Parameter naming

Parameters are exposed in snake_case, derived directly from schema names:

- `userId` becomes `user_id`
- `next_page_token` stays `next_page_token`
- `includeInactive` becomes `include_inactive`

`zoompy` intentionally does **not** invent generic parameter aliases like
`id=`. The public SDK contract stays as close to the schema as possible.

## Typed return values

Normal SDK calls return typed Pydantic models when a representative success
response schema exists.

```python
with ZoomClient() as client:
    user = client.users.get(user_id="me")
    print(user.display_name)
```

When you explicitly want plain validated JSON instead, use `.raw(...)`:

```python
with ZoomClient() as client:
    payload = client.users.get.raw(user_id="me")
```

## Request bodies

Body-capable methods support two common styles:

1. Pass top-level body fields as keyword arguments.
2. Pass a complete payload through `body=`.

```python
with ZoomClient() as client:
    created = client.users.create(
        email="person@example.com",
        first_name="Ada",
    )
```

Or:

```python
with ZoomClient() as client:
    created = client.users.create(
        body={
            "email": "person@example.com",
            "firstName": "Ada",
        }
    )
```

If a generated request model exists, `zoompy` validates the body against that
model before sending it.

## Pagination helpers

Many Zoom list endpoints use `next_page_token`. The SDK keeps that logic close
to the method that owns it.

### `iter_pages(...)`

Yields one typed page at a time:

```python
for page in client.users.list.iter_pages(page_size=100):
    ...
```

### `paginate(...)`

Yields an `SdkPage` object with both items and pagination metadata:

```python
for page in client.users.list.paginate(page_size=100):
    print(page.total_records)
    for user in page.items:
        ...
```

### `iter_all(...)`

Flattens paginated results into one item stream:

```python
for user in client.users.list.iter_all(page_size=100):
    print(user.display_name)
```

## Learning the SDK from tooling

Because the SDK is dynamic, introspection matters more than it would in a
hand-written wrapper library.

Useful tools:

- `inspect.signature(...)`
- `help(...)`
- `.request_model`
- `.response_model`

Those are the main hints the project now optimizes for in code, docs, and
tests.
