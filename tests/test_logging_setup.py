import logging

from backend.logging_setup import RedactDocumentPathsFilter, configure_logging


def _access_record(full_path):
    """Shaped like uvicorn's access log record: args are
    (client_addr, method, full_path, http_version, status_code)."""
    return logging.LogRecord(
        name="uvicorn.access", level=logging.INFO, pathname=__file__, lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:54321", "GET", full_path, "1.1", 200),
        exc_info=None,
    )


def test_given_content_path_when_filtered_then_filename_is_redacted():
    record = _access_record("/api/documents/content/loss_statement_Brooks.pdf?version=3")
    assert RedactDocumentPathsFilter().filter(record) is True
    assert record.args[2] == "/api/documents/content/<redacted>"


def test_given_download_path_when_filtered_then_filename_is_redacted():
    record = _access_record("/api/documents/download/loss_statement_Brooks.pdf")
    RedactDocumentPathsFilter().filter(record)
    assert record.args[2] == "/api/documents/download/<redacted>"


def test_given_claim_path_when_filtered_then_claim_id_is_redacted():
    record = _access_record("/api/documents/claim/%232026-99382")
    RedactDocumentPathsFilter().filter(record)
    assert record.args[2] == "/api/documents/claim/<redacted>"


def test_given_unrelated_path_when_filtered_then_path_is_unchanged():
    record = _access_record("/api/claims?foo=bar")
    RedactDocumentPathsFilter().filter(record)
    assert record.args[2] == "/api/claims"


def test_given_non_access_record_when_filtered_then_left_alone():
    record = logging.LogRecord(
        name="uvicorn.error", level=logging.INFO, pathname=__file__, lineno=1,
        msg="plain message", args=None, exc_info=None,
    )
    assert RedactDocumentPathsFilter().filter(record) is True
    assert record.args is None


def test_configure_logging_attaches_filter_once():
    configure_logging("INFO")
    configure_logging("INFO")
    access_logger = logging.getLogger("uvicorn.access")
    filters = [f for f in access_logger.filters if isinstance(f, RedactDocumentPathsFilter)]
    assert len(filters) == 1
