"""Dependency-free JSON logs with correlated, timed execution spans."""

import json
import logging
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import perf_counter
from uuid import uuid4


@dataclass(frozen=True)
class Span:
    trace_id: str
    span_id: str
    parent_span_id: str | None


_span: ContextVar[Span | None] = ContextVar("execution_span", default=None)
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = _span.get()
        event = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "component": record.name,
            "message": record.getMessage(),
            "trace_id": span.trace_id if span else None,
            "span_id": span.span_id if span else None,
            "parent_span_id": span.parent_span_id if span else None,
        }
        for field in ("event", "operation", "duration_ms", "error_type"):
            if hasattr(record, field):
                event[field] = getattr(record, field)
        # Exception messages and tracebacks can contain credentials or API payloads.
        return json.dumps(event, ensure_ascii=False)


@contextmanager
def configure_logging(log_file: Path, *, level: int = logging.INFO):
    """Attach console/file handlers to our package for the duration of a run."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    package_logger = logging.getLogger("src")
    previous_level = package_logger.level
    previous_propagate = package_logger.propagate
    file_handler = RotatingFileHandler(
        log_file, maxBytes=5_000_000, backupCount=3, encoding="utf-8"
    )
    handlers = (file_handler, logging.StreamHandler(sys.stderr))
    for handler in handlers:
        handler.setFormatter(JsonFormatter())
        package_logger.addHandler(handler)
    package_logger.setLevel(level)
    package_logger.propagate = False
    try:
        yield
    finally:
        for handler in handlers:
            package_logger.removeHandler(handler)
            handler.close()
        package_logger.setLevel(previous_level)
        package_logger.propagate = previous_propagate


@contextmanager
def trace(operation: str):
    """Trace a block or decorated function, inheriting the current run ID."""
    parent = _span.get()
    span = Span(parent.trace_id if parent else uuid4().hex, uuid4().hex[:16],
                parent.span_id if parent else None)
    token = _span.set(span)
    started = perf_counter()
    try:
        logger.info("Started %s", operation, extra={"event": "span.started", "operation": operation})
        try:
            yield span
        except BaseException as exc:
            logger.error("Failed %s", operation, extra={
                "event": "span.failed", "operation": operation,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
                "error_type": type(exc).__name__,
            })
            raise
        else:
            logger.info("Completed %s", operation, extra={
                "event": "span.completed", "operation": operation,
                "duration_ms": round((perf_counter() - started) * 1000, 3),
            })
    finally:
        _span.reset(token)
