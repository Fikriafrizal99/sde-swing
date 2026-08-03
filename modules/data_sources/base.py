from __future__ import annotations

"""Base interfaces for source clients, adapters, and transports.

A *transport* performs raw I/O (HTTP or a mock).  A *source client* wraps a
transport with timeout / retry / rate-limit handling and returns raw payloads.
An *adapter* turns a raw payload into a canonical record via the mapper.  None
of these make trading decisions.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

from modules.data_sources.canonical import CanonicalRecord


class SourceError(Exception):
    """Base class for source-layer errors."""


class SourceTimeout(SourceError):
    pass


class SourceRateLimited(SourceError):
    """The provider rejected the request because its rate limit was hit."""

    def __init__(self, message: str = "source rate limited", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SourceUnavailable(SourceError):
    pass


class SourceUnsupported(SourceError):
    """The provider/source has no verified endpoint for the requested type."""


class SourceRequestInvalid(SourceError):
    """A request cannot be represented by the documented provider contract."""


class SourceNotConfigured(SourceError):
    pass


@dataclass
class TransportResponse:
    status_code: int
    payload: Any
    headers: dict[str, str] = field(default_factory=dict)
    latency_ms: float = 0.0


class Transport(ABC):
    """Raw request/response boundary.  Real HTTP or a deterministic mock."""

    @abstractmethod
    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> TransportResponse:
        raise NotImplementedError


class SourceClient(ABC):
    """Wraps a transport with retry/backoff and produces raw payloads.

    Subclasses implement ``fetch_raw``.  The client is source-aware; the
    adapter that consumes it is the last place that knows the source name.
    """

    name: str = "BASE"

    def __init__(self, *, retry: int = 2, timeout: float = 30.0, backoff_base: float = 0.2):
        self.retry = max(0, int(retry))
        self.timeout = float(timeout)
        self.backoff_base = float(backoff_base)

    @abstractmethod
    def fetch_raw(self, record_type: str, symbol: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    def with_retry(self, fn: Callable[[], Any], *, sleep: Callable[[float], None] = time.sleep) -> Any:
        """Run ``fn`` with bounded retry/backoff on transient errors.

        ``sleep`` is injectable so tests never actually block.
        """
        last_exc: Exception | None = None
        for attempt in range(self.retry + 1):
            try:
                return fn()
            except (SourceTimeout, SourceRateLimited, SourceUnavailable) as exc:
                last_exc = exc
                if attempt >= self.retry:
                    break
                retry_after = getattr(exc, "retry_after", None)
                delay = float(retry_after) if retry_after is not None else self.backoff_base * (2 ** attempt)
                sleep(max(0.0, delay))
        if last_exc is not None:
            raise last_exc
        raise SourceError("with_retry exhausted without result")


class Adapter(ABC):
    """Turns raw source payloads into canonical records.

    This is the last component that knows the concrete source name; everything
    downstream reads only the canonical ``source`` provenance field.
    """

    source_name: str = "BASE"

    @abstractmethod
    def to_canonical(self, record_type: str, raw: Any, **kwargs: Any) -> list[CanonicalRecord]:
        raise NotImplementedError
