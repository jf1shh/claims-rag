"""False-positive check for the answer guard and conflict check: run the golden queries through /api/chat.

The golden queries are honest questions over the real (unpoisoned) corpus, so any answer the
guard withholds or flags, or labels conflicting_evidence, is a candidate false positive -- the cost side of the guard's trade-off.
Requires the app on the real store with a model loaded in LM Studio. Exit 0 whatever the count
(a measurement); 2 only when the app is unreachable.

Usage:
    .venv/bin/python -B eval/golden_guard_check.py [--app-url http://127.0.0.1:8002] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from golden_queries import GOLDEN_QUERIES  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--app-url", default="http://127.0.0.1:8002")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "golden_guard_results.json"))
    args = parser.parse_args(argv)

    try:
        requests.get(args.app_url.rstrip("/") + "/health/ready", timeout=10).raise_for_status()
    except Exception as exc:
        print(f"app not ready at {args.app_url}: {exc}", file=sys.stderr)
        return 2

    records = []
    for query in GOLDEN_QUERIES:
        try:
            response = requests.post(
                args.app_url.rstrip("/") + "/api/chat",
                json={"query": query["query"], "claim_id": query["claim_id"], "engine": "lm-studio"},
                timeout=180,
            )
            response.raise_for_status()
            body = response.json()
            error = None
        except Exception as exc:
            body, error = {}, f"{type(exc).__name__}: {exc}"
        guard = body.get("guard")
        records.append({
            "id": query["id"],
            "error": error,
            "guard_action": guard["action"] if guard else None,
            "findings": guard["findings"] if guard else [],
            "status": ((body.get("structured") or {}).get("answer") or {}).get("status"),
            "sources": [src.get("filename") for src in body.get("sources") or [] if isinstance(src, dict)],
            "answer": body.get("answer") or "",
            "conflict": ((body.get("structured") or {}).get("answer") or {}).get("conflict"),
        })
        print(f"{query['id']:<40} guard={records[-1]['guard_action']} status={records[-1]['status']}"
              + (f" ERROR {error}" if error else ""))

    touched = [r for r in records if r["guard_action"]]
    summary = {"n": len(records), "errors": sum(1 for r in records if r["error"]),
               "withheld": sum(1 for r in touched if r["guard_action"] == "withheld"),
               "flagged": sum(1 for r in touched if r["guard_action"] == "flagged"),
               "conflicting_evidence": sum(1 for r in records if r["status"] == "conflicting_evidence")}
    Path(args.out).write_text(json.dumps(
        {"run_at": datetime.now(timezone.utc).isoformat(), "app_url": args.app_url,
         "summary": summary, "records": records}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary))
    for record in touched:
        for finding in record["findings"]:
            print(f"  FP? {record['id']}: {finding['kind']}:{finding['category']} :: {finding['excerpt'][:160]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
