"""Shared SDE Swing V1.7.0 runtime primitives.

The legacy command modules remain import-compatible, while every new job can
obtain the same context, source manager, status writer and Telegram router
through this package.
"""

from .context import RuntimeContext, RuntimePaths
from .data_source_manager import DataSourceManager, ProviderMetadata
from .status import ALLOWED_JOB_STATUSES, StatusWriter, build_status_payload

__all__ = [
    "ALLOWED_JOB_STATUSES",
    "DataSourceManager",
    "ProviderMetadata",
    "RuntimeContext",
    "RuntimePaths",
    "StatusWriter",
    "build_status_payload",
]
