"""Public package exports for the `zoompy` library.

`zoompy` intentionally exposes a small top-level import surface:

* :class:`ZoomClient` is the main entry point for both the low-level request
  API and the higher-level dynamic SDK.
* :func:`configure_logging` enables the package's structured JSON logging.
* :class:`WebhookRegistry` is available for advanced callers that want direct
  runtime webhook validation control outside the client.

The dynamic SDK itself hangs off :class:`ZoomClient` instances, so users do not
need to import a large forest of generated service classes. In practice the
most common import looks like:

.. code-block:: python

    from zoompy import ZoomClient

    with ZoomClient() as client:
        users = client.users.list(page_size=10)
"""

from .client import ZoomClient
from .logging import configure_logging
from .schema import WebhookRegistry
from .sdk import ZoomSdk

__version__ = "0.1.0"

__all__ = [
    "ZoomClient",
    "ZoomSdk",
    "WebhookRegistry",
    "configure_logging",
    "__version__",
]
