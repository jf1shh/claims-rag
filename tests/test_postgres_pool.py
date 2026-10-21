"""M12c-B: connection pool with connect/statement/checkout timeouts.

Offline tests (no DB) cover construction, laziness, and close() idempotency.
Postgres-gated tests prove pooling, the tenant GUC, statement_timeout, and
checkout starvation against a real database, following the skip pattern in
tests/test_postgres_store.py.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.postgres_store import PostgresVectorStore  # noqa: E402

POSTGRES_DSN = os.environ.get("POSTGRES_DSN")
needs_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="POSTGRES_DSN not set; set it to a reachable Postgres + pgvector to run the PG backend tests",
)


def _provision_schema(dsn: str) -> None:
    """Runs `alembic upgrade head` against the DSN, same as test_postgres_store.py,
    so this file is self-contained and does not depend on collection order."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    old = os.environ.get("POSTGRES_DSN")
    os.environ["POSTGRES_DSN"] = dsn
    try:
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("POSTGRES_DSN", None)
        else:
            os.environ["POSTGRES_DSN"] = old


@pytest.fixture(scope="module")
def provisioned_db():
    _provision_schema(POSTGRES_DSN)
    return POSTGRES_DSN


# --------------------------------------------------------------------------- #
# Offline: no DB required
# --------------------------------------------------------------------------- #

def test_constructing_a_store_opens_no_connection():
    store = PostgresVectorStore(dsn="postgresql://localhost/does-not-matter")
    assert store._pool is None
    store.close()  # idempotent even though nothing was ever opened


def test_close_is_idempotent_with_no_pool():
    store = PostgresVectorStore(dsn="postgresql://localhost/does-not-matter")
    store.close()
    store.close()
    assert store._pool is None


class _FakeRoleRow:
    def __init__(self, bypasses):
        self._bypasses = bypasses

    def fetchone(self):
        return (self._bypasses,)


class _FakeConn:
    def __init__(self, bypasses):
        self._bypasses = bypasses
        self.committed = False

    def execute(self, *args, **kwargs):
        return _FakeRoleRow(self._bypasses)

    def commit(self):
        self.committed = True


def test_check_rls_bypass_warns_once_when_role_bypasses_rls(caplog):
    import logging

    store = PostgresVectorStore(dsn="postgresql://localhost/does-not-matter")
    with caplog.at_level(logging.WARNING):
        store._check_rls_bypass(_FakeConn(bypasses=True))

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "bypasses row-level security" in warnings[0].message


def test_check_rls_bypass_is_silent_when_role_does_not_bypass_rls(caplog):
    import logging

    store = PostgresVectorStore(dsn="postgresql://localhost/does-not-matter")
    with caplog.at_level(logging.WARNING):
        store._check_rls_bypass(_FakeConn(bypasses=False))

    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_pool_settings_are_stored_from_constructor_kwargs():
    store = PostgresVectorStore(
        dsn="postgresql://localhost/does-not-matter",
        pool_connect_timeout_seconds=2,
        pool_statement_timeout_ms=5000,
        pool_max_size=3,
        pool_timeout_seconds=0.5,
    )
    assert store.pool_connect_timeout_seconds == 2
    assert store.pool_statement_timeout_ms == 5000
    assert store.pool_max_size == 3
    assert store.pool_timeout_seconds == 0.5


# --------------------------------------------------------------------------- #
# Postgres-gated: pooling, tenant GUC, statement_timeout, checkout starvation
# --------------------------------------------------------------------------- #

@needs_postgres
def test_sequential_calls_reuse_pooled_connections(provisioned_db):
    store = PostgresVectorStore(dsn=provisioned_db, tenant_id="pool-reuse-tenant")
    try:
        for _ in range(10):
            store.get_all_documents()
        stats = store._get_pool().get_stats()
        assert stats["connections_num"] < 10
    finally:
        store.close()


@needs_postgres
def test_pooled_connection_has_tenant_guc_set(provisioned_db):
    store = PostgresVectorStore(dsn=provisioned_db, tenant_id="pool-guc-tenant")
    try:
        conn = store._connect()
        try:
            value = conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0]
            assert value == "pool-guc-tenant"
        finally:
            conn.close()
    finally:
        store.close()


@needs_postgres
def test_statement_timeout_is_applied_to_pooled_connections(provisioned_db):
    store = PostgresVectorStore(dsn=provisioned_db, tenant_id="pool-timeout-tenant", pool_statement_timeout_ms=5000)
    try:
        conn = store._connect()
        try:
            value = conn.execute("SHOW statement_timeout").fetchone()[0]
            assert value == "5s"
        finally:
            conn.close()
    finally:
        store.close()


@needs_postgres
def test_checkout_times_out_when_pool_is_exhausted(provisioned_db):
    from psycopg_pool import PoolTimeout

    store = PostgresVectorStore(
        dsn=provisioned_db,
        tenant_id="pool-starve-tenant",
        pool_max_size=1,
        pool_timeout_seconds=0.5,
    )
    held = threading.Event()
    release = threading.Event()

    def hold_connection():
        conn = store._connect()
        try:
            held.set()
            release.wait(timeout=5)
        finally:
            conn.close()

    holder = threading.Thread(target=hold_connection)
    holder.start()
    try:
        held.wait(timeout=5)
        start = time.monotonic()
        with pytest.raises(PoolTimeout):
            store._connect()
        elapsed = time.monotonic() - start
        assert 0.3 < elapsed < 3.0
    finally:
        release.set()
        holder.join(timeout=5)
        store.close()


@needs_postgres
def test_close_rolls_back_uncommitted_work_instead_of_committing(provisioned_db):
    # max_size=1 so the second checkout reuses the same physical connection.
    # set_config(..., false) is session-scoped but transactional: it survives
    # only if the first checkout's transaction was committed.
    store = PostgresVectorStore(dsn=provisioned_db, tenant_id="pool-discard-tenant", pool_max_size=1)
    try:
        conn = store._connect()
        conn.execute("SELECT set_config('app.pool_probe', 'dirty', false)")
        conn.close()
        conn = store._connect()
        try:
            value = conn.execute("SELECT current_setting('app.pool_probe', true)").fetchone()[0]
            assert value != "dirty"
        finally:
            conn.close()
    finally:
        store.close()


@needs_postgres
def test_force_row_level_security_is_applied_to_rls_tables(provisioned_db):
    import psycopg

    with psycopg.connect(provisioned_db, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT relname, relforcerowsecurity FROM pg_class "
            "WHERE relname IN ('documents', 'parent_chunks', 'child_chunks')"
        ).fetchall()
    assert len(rows) == 3
    assert all(forced for _, forced in rows)


@needs_postgres
def test_close_after_failed_statement_returns_a_usable_connection(provisioned_db):
    from psycopg.errors import UndefinedTable

    store = PostgresVectorStore(dsn=provisioned_db, tenant_id="pool-error-tenant", pool_max_size=1)
    try:
        conn = store._connect()
        with pytest.raises(UndefinedTable):
            conn.execute("SELECT * FROM no_such_table_m12c")
        conn.close()
        conn = store._connect()
        try:
            assert conn.execute("SELECT current_setting('app.tenant_id', true)").fetchone()[0] == "pool-error-tenant"
        finally:
            conn.close()
    finally:
        store.close()
