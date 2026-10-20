"""Request-local tracing, independent from business services."""

from contextvars import ContextVar

request_id: ContextVar[str] = ContextVar("request_id", default="")
