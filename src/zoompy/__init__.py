"""Public package exports for the `zoompy` library.

The top-level namespace stays intentionally small even as the library grows.
Users generally need the main client, optional logging configuration, and the
runtime webhook registry. The dynamic SDK surface hangs off `ZoomClient`
instances directly, so it does not need a large import surface here.
"""

from .client import ZoomClient
from .logging import configure_logging
from .sdk import ZoomSdk
from .schema import WebhookRegistry

__all__ = ["ZoomClient", "ZoomSdk", "WebhookRegistry", "configure_logging"]
