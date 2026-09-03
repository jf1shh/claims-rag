"""Phase 6.1: Locust load test for retrieval latency at scale.

Drives concurrent, multi-tenant query load against a live `backend.app:app`
process (real HTTP, real auth/RBAC, real PostgresVectorStore/HNSW retrieval)
seeded by scripts/seed_retrieval_load_corpus.py. Measures p50/p95/p99 via
Locust's own stats -- no custom latency-tracking code.

Two tasks, weighted: /api/chat with engine=simulated (exercises the full
retrieval path without requiring a live LM Studio -- retrieval cost is what
Phase 6.1 measures, not LLM synthesis latency) and /api/eval/search (raw
retrieval, no synthesis at all).

Credentials: reads LOADTEST_SERVICE_ACCOUNTS_FILE (JSON, the same file passed
as SERVICE_ACCOUNTS_FILE to the server -- see scripts/run_retrieval_load_test.sh),
picks one API key per simulated user so load spreads across every seeded
tenant rather than hammering one principal (which would test the per-principal
rate limiter, not retrieval).

Run (via the orchestration script, not directly):
    locust -f scripts/locustfile_retrieval.py --headless -u 50 -r 10 -t 60s \
        --host http://127.0.0.1:8000 --csv results/loadtest
"""

from __future__ import annotations

import json
import os
import random

from locust import HttpUser, between, task

_QUERIES = [
    "What is the mechanical labor cap for this region?",
    "What is the sheet metal and refinishing labor rate?",
    "Does the glass endorsement waive the comprehensive deductible?",
    "What state statute applies to this claim?",
    "What is the regional labor rate schedule?",
]


def _load_credentials() -> list[tuple[str, str]]:
    """Returns [(api_key, tenant_id), ...] from LOADTEST_SERVICE_ACCOUNTS_FILE."""
    path = os.environ["LOADTEST_SERVICE_ACCOUNTS_FILE"]
    accounts = json.loads(open(path, encoding="utf-8").read())
    return [(key, fields["tenant_id"]) for key, fields in accounts.items()]


class RetrievalUser(HttpUser):
    wait_time = between(0.05, 0.25)

    def on_start(self):
        credentials = _load_credentials()
        api_key, self.tenant_id = random.choice(credentials)
        self.client.headers.update({"X-API-Key": api_key})

    @task(3)
    def chat(self):
        query = random.choice(_QUERIES)
        with self.client.post(
            "/api/chat",
            json={"query": query, "engine": "simulated"},
            name="/api/chat",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"status {response.status_code}: {response.text[:200]}")

    @task(1)
    def eval_search(self):
        # mode=hybrid_rerank, not the cheaper hybrid/naive modes: this must
        # match /api/chat's actual production retrieval path (which always
        # reranks) or the measured p95 would understate real user-facing
        # latency by skipping the single most expensive step.
        query = random.choice(_QUERIES)
        with self.client.post(
            "/api/eval/search",
            json={"query": query, "mode": "hybrid_rerank", "top_k": 4},
            name="/api/eval/search",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"status {response.status_code}: {response.text[:200]}")
