"""Request-log hygiene: applies the configured log level once at startup and
strips PII-adjacent path segments from uvicorn's access log."""
from __future__ import annotations

import logging

_REDACT_PREFIXES = (
    "/api/documents/content/",
    "/api/documents/download/",
    "/api/documents/claim/",
)


class RedactDocumentPathsFilter(logging.Filter):
    """Uvicorn's access logger logs the request target as a %-style arg tuple
    (client_addr, method, full_path, http_version, status_code). Filenames
    carry claimant surnames and claim ids are PII-adjacent, so the path
    segment after each of ``_REDACT_PREFIXES`` is replaced with
    ``<redacted>``; the query string is dropped outright since it can hold
    the same identifiers."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3 or not isinstance(args[2], str):
            return True
        path = args[2].split("?", 1)[0]
        for prefix in _REDACT_PREFIXES:
            if path.startswith(prefix):
                path = prefix + "<redacted>"
                break
        record.args = (*args[:2], path, *args[3:])
        return True


def configure_logging(log_level: str) -> None:
    """Applies LOG_LEVEL (parsed by config.Settings but never used before) to
    the root logger, and attaches the redaction filter to uvicorn's access
    logger. Idempotent so repeated app creation (every test building its own
    app) doesn't stack duplicate filters."""
    logging.getLogger().setLevel(log_level)
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactDocumentPathsFilter) for f in access_logger.filters):
        access_logger.addFilter(RedactDocumentPathsFilter())
