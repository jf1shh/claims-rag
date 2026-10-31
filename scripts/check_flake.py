"""Known-flake triage for a CI run's failed-step log (tests/known_flakes.json).

CI occasionally fails on a known-flaky assertion (see tests/test_ingestion_load.py's
throughput check) that has been re-diagnosed from scratch across multiple past session
handoffs. This tool matches a run's failure output against a small, maintained registry
(tests/known_flakes.json) so a session triaging a red run gets an immediate answer:
"known flake, re-run" vs "novel failure, investigate" -- instead of re-deriving it.

Usage:
    scripts/check_flake.py <run-id>
        Fetches `gh run view <run-id> --log-failed` and checks it.

    <some-log-source> | scripts/check_flake.py -
        Reads failure log text from stdin instead of calling `gh` (useful offline
        or in tests). Pass "-" explicitly, or omit the run-id entirely when stdin
        is not a tty.

Exit code: 0 if every FAILED test line either matched a known flake or had no
registry entry to compare against failure text for (best-effort: see "Matching"
below); non-zero if any FAILED test is a *novel* failure -- a registered nodeid
whose failure text does NOT match its known signature, or an unregistered nodeid
with no matching entry at all. This makes it usable as a CI gate decision helper:
a non-zero exit means a human should look at this run.

Matching: a pytest log's "FAILED <nodeid> - ..." summary lines are extracted to find
which tests failed. For each, the full log text is checked against every known_flakes.json
entry whose nodeid matches; if the entry's `signature` (a regex) is found anywhere in the
log text, that failure is reported as a known flake. A failed nodeid with no matching
registry entry, or whose failure text doesn't match the registered signature, is reported
as novel.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REGISTRY = ROOT / "tests" / "known_flakes.json"

# Matches pytest's "short test summary info" lines: "FAILED <nodeid> - <reason>".
# Not anchored to line start: a raw `pytest` run has no prefix, but `gh run view
# --log-failed` prepends a "<job> <step> <timestamp> " prefix to every line, and
# that prefix's own length/shape varies by job name and step.
FAILED_LINE_RE = re.compile(r"FAILED\s+(\S+::\S+)\s+-")


def load_registry(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    return data.get("flakes", [])


def fetch_run_log(run_id: str) -> str:
    """Fetch a run's failed-step log via the gh CLI."""
    result = subprocess.run(
        ["gh", "run", "view", run_id, "--log-failed"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh run view {run_id} --log-failed failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    return result.stdout


def extract_failed_nodeids(log_text: str) -> list[str]:
    """Pull pytest nodeids out of 'FAILED <nodeid> - ...' summary lines.

    Deduplicates while preserving first-seen order (a log can repeat the summary
    line, e.g. once inline during the run and once in "short test summary info").
    """
    seen: dict[str, None] = {}
    for match in FAILED_LINE_RE.finditer(log_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def classify(log_text: str, registry: list[dict]) -> dict:
    """Classify each failed nodeid in log_text as known-flaky or novel.

    Returns {"known": [...], "novel": [...]} where each entry is
    {"nodeid": str, "reason"/"detail": str}.
    """
    failed_nodeids = extract_failed_nodeids(log_text)
    by_nodeid: dict[str, list[dict]] = {}
    for entry in registry:
        by_nodeid.setdefault(entry["nodeid"], []).append(entry)

    known = []
    novel = []
    for nodeid in failed_nodeids:
        candidates = by_nodeid.get(nodeid, [])
        matched_entry = None
        for entry in candidates:
            if re.search(entry["signature"], log_text):
                matched_entry = entry
                break
        if matched_entry is not None:
            known.append({"nodeid": nodeid, "reason": matched_entry["reason"]})
        elif candidates:
            novel.append(
                {
                    "nodeid": nodeid,
                    "detail": "registered flake exists for this test, but its failure "
                    "signature did not match this run's output -- treat as a new failure mode",
                }
            )
        else:
            novel.append({"nodeid": nodeid, "detail": "no known_flakes.json entry for this test"})

    return {"known": known, "novel": novel, "total_failed": len(failed_nodeids)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "run_id",
        nargs="?",
        default=None,
        help="CI run id to fetch via `gh run view <run-id> --log-failed`. Omit or pass '-' to read log text from stdin.",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_REGISTRY,
        help=f"path to the known-flakes registry (default: {DEFAULT_REGISTRY.relative_to(ROOT)})",
    )
    args = parser.parse_args(argv)

    if args.run_id in (None, "-"):
        log_text = sys.stdin.read()
    else:
        try:
            log_text = fetch_run_log(args.run_id)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    registry = load_registry(args.registry)
    result = classify(log_text, registry)

    if result["total_failed"] == 0:
        print("No FAILED test lines found in the log.")
        return 0

    for item in result["known"]:
        print(f"KNOWN FLAKE: {item['nodeid']}\n  {item['reason']}")
    for item in result["novel"]:
        print(f"NOVEL FAILURE: {item['nodeid']}\n  {item['detail']}")

    print(
        f"\n{len(result['known'])} known flake(s), {len(result['novel'])} novel failure(s), "
        f"{result['total_failed']} total failed test(s)."
    )

    return 1 if result["novel"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
