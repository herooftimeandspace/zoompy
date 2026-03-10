"""Public package exports for the `zoompy` library.

The package is intentionally small at the top level. Most of the interesting
behavior lives in the client, schema registry, and logging helpers. Keeping the
top-level namespace compact makes the library easier to discover for new users.
"""

from .client import ZoomClient
from .logging import configure_logging
from .schema import WebhookRegistry

__all__ = ["ZoomClient", "WebhookRegistry", "configure_logging"]
