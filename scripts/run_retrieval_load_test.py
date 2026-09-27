"""Phase 6.1: orchestrates one end-to-end retrieval load-test run.

Provisions (or reuses) a Postgres + pgvector instance, seeds a multi-tenant
corpus, starts the real backend.app:app server against it, drives concurrent
query load with Locust, and reports p50/p95/p99 latency against the
configured bar.

If POSTGRES_DSN is not set and --postgres-dsn is not passed, starts a
throwaway `pgvector/pgvector:pg16` Docker container and tears it down
afterward -- matching the exact image the `postgres` CI job's service
container already uses. When a DSN *is* provided (e.g. CI's own service
container, or a developer's existing instance), this script never touches
its lifecycle.

CI smoke:    python scripts/run_retrieval_load_test.py --count 500 --tenants 2 \
                 --users 5 --spawn-rate 5 --duration 15s --no-assert-p95 --synthetic-models
Real 6.1:    python scripts/run_retrieval_load_test.py --count 100000 --tenants 8 \
                 --users 50 --spawn-rate 10 --duration 120s
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _provision_schema(dsn: str) -> None:
    """Runs `alembic upgrade head` in-process (mirrors
    tests/test_postgres_store.py's _provision_schema)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    command.upgrade(cfg, "head")


def _start_docker_postgres(port: int) -> str:
    name = f"claimsrag-loadtest-pg-{secrets.token_hex(4)}"
    subprocess.run(
        [
            "docker", "run", "-d", "--name", name,
            "-p", f"{port}:5432",
            "-e", "POSTGRES_USER=postgres", "-e", "POSTGRES_PASSWORD=postgres", "-e", "POSTGRES_DB=rag",
            "pgvector/pgvector:pg16",
        ],
        check=True, capture_output=True,
    )
    dsn = f"postgresql://postgres:postgres@localhost:{port}/rag"
    for _ in range(30):
        result = subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"], capture_output=True)
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise RuntimeError(f"Postgres container {name} never became ready")
    return name, dsn


def _stop_docker_postgres(name: str) -> None:
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _write_service_accounts(path: Path, tenant_ids: list[str]) -> dict:
    accounts = {
        f"loadtest-key-{secrets.token_hex(12)}": {"subject": f"loadtest-{t}", "tenant_id": t, "roles": ["admin"]}
        for t in tenant_ids
    }
    path.write_text(json.dumps(accounts), encoding="utf-8")
    return accounts


def _start_server(dsn: str, service_accounts_path: Path, port: int, *, synthetic_models: bool = False) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "VECTOR_STORE": "postgres",
            "TENANT_ID": next(iter(json.loads(service_accounts_path.read_text()).values()))["tenant_id"],
            "POSTGRES_DSN": dsn,
            "AUTH_PROVIDERS": "service-accounts",
            "SERVICE_ACCOUNTS_FILE": str(service_accounts_path),
            # This test measures retrieval latency, not the rate limiter --
            # raise the per-principal allowance well above what concurrent
            # simulated users would otherwise trip.
            "RATE_LIMIT_MAX_REQUESTS": "1000000",
            "RATE_LIMIT_WINDOW_SECONDS": "60",
        }
    )
    app_target = "scripts.retrieval_smoke_app:app" if synthetic_models else "backend.app:app"
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", app_target, "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def _wait_for_health(port: int, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/live", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"server on port {port} never became healthy")


def _warm_up(port: int, api_key: str) -> None:
    """Fires one sequential /api/chat request before the timed Locust run.

    The embedding + reranker models load lazily on first use (by design --
    see backend/app.py's HF_HUB_OFFLINE / Critical Constraints doc); the first
    request after a fresh server start pays that one-time cost (observed
    ~2.2s locally). Without a warm-up, concurrent simulated users all racing
    to trigger that same lazy load on their first request causes severe,
    misleading contention (observed p95 spiking to several seconds on an
    otherwise-~400ms-steady-state server) -- measuring cold-start contention,
    not sustained retrieval latency. Standard load-test practice separates
    the two; this one sequential request is that separation.
    """
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/chat",
        data=json.dumps({"query": "warm up", "engine": "simulated"}).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30):
        pass


def _run_locust(port: int, users: int, spawn_rate: int, duration: str, csv_prefix: str, service_accounts_path: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["LOADTEST_SERVICE_ACCOUNTS_FILE"] = str(service_accounts_path)
    cmd = [
        sys.executable, "-m", "locust",
        "-f", str(ROOT / "scripts" / "locustfile_retrieval.py"),
        "--headless", "-u", str(users), "-r", str(spawn_rate), "-t", duration,
        "--host", f"http://127.0.0.1:{port}",
        "--csv", csv_prefix,
    ]
    return subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True, text=True)


def _parse_p95_ms(csv_prefix: str) -> float:
    stats_path = Path(f"{csv_prefix}_stats.csv")
    with open(stats_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["Name"] == "Aggregated":
                return float(row["95%"])
    raise RuntimeError(f"no 'Aggregated' row found in {stats_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--tenants", type=int, default=8)
    parser.add_argument("--tenant-prefix", default="loadtest-tenant")
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--spawn-rate", type=int, default=10)
    parser.add_argument("--duration", default="60s", help="Locust run duration, e.g. 60s, 5m")
    parser.add_argument("--p95-bar-ms", type=float, default=100.0)
    parser.add_argument("--assert-p95", dest="assert_p95", action="store_true", default=True)
    parser.add_argument("--no-assert-p95", dest="assert_p95", action="store_false")
    parser.add_argument("--synthetic-models", action="store_true", help="offline orchestration smoke only; requires --no-assert-p95")
    parser.add_argument("--postgres-dsn", default=None, help="reuse an existing Postgres instance instead of starting one")
    parser.add_argument("--server-port", type=int, default=8099)
    parser.add_argument("--docker-port", type=int, default=15433)
    args = parser.parse_args(argv)
    if args.synthetic_models and args.assert_p95:
        parser.error("synthetic models require --no-assert-p95; they cannot validate model latency")
    if args.synthetic_models:
        print("SYNTHETIC MODELS: orchestration smoke only; not production latency evidence")

    dsn = args.postgres_dsn or os.environ.get("POSTGRES_DSN")
    container_name = None
    if not dsn:
        print("No POSTGRES_DSN given -- starting a throwaway pgvector/pgvector:pg16 container...")
        container_name, dsn = _start_docker_postgres(args.docker_port)

    server = None
    try:
        print("Provisioning schema (alembic upgrade head)...")
        _provision_schema(dsn)

        tenant_ids = [f"{args.tenant_prefix}-{i}" for i in range(args.tenants)]
        with tempfile.TemporaryDirectory() as tmp:
            sa_path = Path(tmp) / "service_accounts.json"
            accounts = _write_service_accounts(sa_path, tenant_ids[:1])
            warm_up_key = next(iter(accounts))

            print(f"Seeding {args.count} docs across {args.tenants} tenants...")
            seed_result = subprocess.run(
                [
                    sys.executable, str(ROOT / "scripts" / "seed_retrieval_load_corpus.py"),
                    "--count", str(args.count), "--tenants", str(args.tenants),
                    "--postgres-dsn", dsn, "--tenant-prefix", args.tenant_prefix,
                ],
                cwd=str(ROOT), capture_output=True, text=True,
            )
            print(seed_result.stdout)
            if seed_result.returncode != 0:
                print(seed_result.stderr, file=sys.stderr)
                return 1

            print("Starting server...")
            server = _start_server(dsn, sa_path, args.server_port, synthetic_models=args.synthetic_models)
            _wait_for_health(args.server_port)
            print("Warming up (one sequential request to force lazy model loading)...")
            _warm_up(args.server_port, warm_up_key)

            csv_prefix = str(Path(tmp) / "loadtest")
            print(f"Running Locust: {args.users} users, spawn rate {args.spawn_rate}, duration {args.duration}...")
            locust_result = _run_locust(args.server_port, args.users, args.spawn_rate, args.duration, csv_prefix, sa_path)
            print(locust_result.stdout[-4000:])
            if locust_result.returncode not in (0, 1):
                # Locust exits 1 when any request failed during the run, which we
                # still want to inspect (stats are written either way); any other
                # code means the harness itself broke.
                print(locust_result.stderr, file=sys.stderr)
                return 1

            p95 = _parse_p95_ms(csv_prefix)
            print(f"P95={p95:.1f}ms bar={args.p95_bar_ms:.1f}ms count={args.count} tenants={args.tenants} users={args.users}")
            if args.assert_p95 and p95 > args.p95_bar_ms:
                print("LOAD TEST FAILED: p95 exceeded bar")
                return 1
            print("LOAD TEST PASSED")
            return 0
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if container_name:
            print(f"Stopping throwaway container {container_name}...")
            _stop_docker_postgres(container_name)


if __name__ == "__main__":
    raise SystemExit(main())
