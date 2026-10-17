"""Compact dev CLI for ClaimsRAG.

Every subcommand prints a short, fixed-size summary instead of raw logs or
JSON, so an agent can read one screen instead of paging through output.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DOMAINS: list[tuple[str, list[str]]] = [
    ("total loss", ["total loss", "totaled", "actual cash value", "ACV"]),
    ("subrogation", ["subrogat"]),
    ("supplement review", ["supplement"]),
    ("salvage", ["salvage"]),
    ("diminished value", ["diminished value"]),
    ("betterment", ["betterment"]),
    ("OEM parts", ["OEM"]),
    ("labor rate", ["labor rate"]),
    ("rental", ["rental"]),
    ("deductible", ["deductible"]),
    ("fraud/SIU", ["fraud", "SIU"]),
    ("liability", ["liability", "at-fault"]),
    ("storage/towing", ["storage", "towing"]),
    ("glass", ["glass"]),
    ("PII/privacy", ["PII", "personal information"]),
]

GATE_STATUS_RE = re.compile(r"foundation gate (passed|failed)")
GATE_FINDING_RE = re.compile(r"(\d+) finding")
GATE_BLOCKING_RE = re.compile(r"(\d+) blocking")
WARNING_GATE_RE = re.compile(r"^\[warning\] ([^:]+):")


def find_python(root: Path) -> str:
    venv_py = root / ".venv" / "bin" / "python"
    if venv_py.exists():
        return str(venv_py)
    return sys.executable


def run_tests(py: str, root: Path) -> bool:
    proc = subprocess.run(
        [py, "-m", "pytest", "tests/", "-q", "-p", "no:cacheprovider"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    lines = (proc.stdout + proc.stderr).splitlines()
    non_empty = [line for line in lines if line.strip()]
    print(non_empty[-1] if non_empty else "(no pytest output)")
    fail_lines = [line for line in lines if line.startswith("FAILED") or line.startswith("ERROR")]
    for line in fail_lines[:15]:
        print(line)
    return proc.returncode != 0


def run_gate(py: str, root: Path) -> bool:
    proc = subprocess.run(
        [py, "scripts/run_foundation_gates.py", "--mode", "gate"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    lines = (proc.stdout + proc.stderr).splitlines()

    status_line = next(
        (line for line in lines if "foundation gate passed:" in line or "foundation gate failed:" in line),
        None,
    )
    if status_line is not None:
        status_match = GATE_STATUS_RE.search(status_line)
        status = status_match.group(1) if status_match else "?"
        finding_match = GATE_FINDING_RE.search(status_line)
        n_findings = finding_match.group(1) if finding_match else "?"
        blocking_match = GATE_BLOCKING_RE.search(status_line)
        n_blocking = blocking_match.group(1) if blocking_match else "?"
        print(f"gate: {status}, {n_findings} findings, {n_blocking} blocking")
    else:
        print("gate: unknown")

    error_lines = [line for line in lines if line.startswith("[error]")]
    for line in error_lines[:10]:
        print(line)

    warning_counts: dict[str, int] = {}
    for line in lines:
        if line.startswith("[warning]"):
            match = WARNING_GATE_RE.match(line)
            key = match.group(1) if match else "other"
            warning_counts[key] = warning_counts.get(key, 0) + 1
    if warning_counts:
        parts = ", ".join(f"{key} {count}" for key, count in sorted(warning_counts.items()))
        print(f"warnings: {parts}")

    return proc.returncode != 0


def cmd_check(args: argparse.Namespace, root: Path) -> int:
    py = find_python(root)
    failed = False
    if not args.gate_only:
        failed = run_tests(py, root) or failed
    if not args.tests_only:
        failed = run_gate(py, root) or failed
    return 1 if failed else 0


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def cmd_evals(root: Path) -> int:
    results_path = root / "eval" / "results.json"
    data = _load_json(results_path) if results_path.exists() else None
    if data is not None:
        summary = data.get("summary", {})
        file_date = datetime.fromtimestamp(results_path.stat().st_mtime).strftime("%Y-%m-%d")
        print(
            f"golden: n={summary.get('n_queries', '?')} model={summary.get('model', '?')} "
            f"hybrid P/R={summary.get('hybrid_precision_avg', '?')}/{summary.get('hybrid_recall_avg', '?')} "
            f"faithfulness={summary.get('faithfulness_avg', '?')} correctness={summary.get('correctness_avg', '?')} "
            f"(file date {file_date} from mtime)"
        )

    eval_dir = root / "eval"
    if eval_dir.is_dir():
        for path in sorted(eval_dir.glob("adversarial_results*.json")):
            data = _load_json(path)
            if data is None:
                continue
            aggregates = data.get("aggregates", {})
            overall = aggregates.get("overall", {})
            injection = aggregates.get("injection", {})
            conflict = aggregates.get("conflict", {})
            run_at = (data.get("run_at") or "")[:10] or "?"
            print(
                f"{path.stem}: run {run_at} n={data.get('n_cases', '?')} "
                f"overall {overall.get('pass_rate', '?')} injection {injection.get('pass_rate', '?')} "
                f"(attack success {injection.get('attack_success_rate', '?')}) "
                f"conflict {conflict.get('pass_rate', '?')}"
            )

    guard_path = root / "eval" / "golden_guard_results.json"
    data = _load_json(guard_path) if guard_path.exists() else None
    if data is not None:
        summary = data.get("summary", {})
        scalar_pairs = [
            f"{key}={value}" for key, value in summary.items() if isinstance(value, (int, float, str, bool, type(None)))
        ]
        print(f"guard: {' '.join(scalar_pairs)}")

    return 0


def _count_keyword_hits(text: str, keywords: list[str]) -> int:
    lowered = text.lower()
    total = 0
    for keyword in keywords:
        if keyword == "ACV":
            total += len(re.findall(r"\bacv\b", lowered))
        else:
            total += lowered.count(keyword.lower())
    return total


def _count_golden_queries(path: Path) -> int:
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "GOLDEN_QUERIES":
                    if isinstance(node.value, (ast.List, ast.Tuple)):
                        return len(node.value.elts)
    return 0


def cmd_coverage(root: Path) -> int:
    golden_path = root / "eval" / "golden_queries.py"
    golden_text = golden_path.read_text() if golden_path.exists() else ""
    n_golden_queries = _count_golden_queries(golden_path) if golden_path.exists() else 0

    adversarial_dir = root / "eval" / "adversarial"
    adversarial_text_parts: list[str] = []
    if adversarial_dir.is_dir():
        for path in sorted(adversarial_dir.rglob("*")):
            if path.is_file():
                try:
                    adversarial_text_parts.append(path.read_text(errors="ignore"))
                except OSError:
                    continue
    adversarial_text = "\n".join(adversarial_text_parts)

    print(f"golden queries: {n_golden_queries}")
    print("domain | golden | adversarial | flag")
    for name, keywords in DOMAINS:
        golden_hits = _count_keyword_hits(golden_text, keywords)
        adversarial_hits = _count_keyword_hits(adversarial_text, keywords)
        flag = "GAP" if golden_hits == 0 else "thin" if golden_hits == 1 else ""
        print(f"{name} | {golden_hits} | {adversarial_hits} | {flag}")
    return 0


def _check_url(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=1) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def cmd_status(root: Path) -> int:
    def git(args: list[str]) -> str:
        try:
            proc = subprocess.run(["git", "-C", str(root)] + args, capture_output=True, text=True)
            return proc.stdout.strip()
        except OSError:
            return "?"

    branch = git(["rev-parse", "--abbrev-ref", "HEAD"]) or "?"
    porcelain = git(["status", "--porcelain"])
    n_changed = len([line for line in porcelain.splitlines() if line.strip()])
    last_commit = git(["log", "-1", "--oneline"]) or "?"

    print(f"branch: {branch}")
    print(f"changed files: {n_changed}")
    print(f"last commit: {last_commit}")
    print(f"backend: {'up' if _check_url('http://localhost:8000/api/status') else 'down'}")
    print(f"lemonade: {'up' if _check_url('http://localhost:13305/api/v1/models') else 'down'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crag", description="Compact dev CLI for ClaimsRAG")
    parser.add_argument("--root", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check")
    check_group = check_parser.add_mutually_exclusive_group()
    check_group.add_argument("--tests-only", action="store_true")
    check_group.add_argument("--gate-only", action="store_true")

    subparsers.add_parser("evals")
    subparsers.add_parser("coverage")
    subparsers.add_parser("status")

    args = parser.parse_args(argv)
    root = args.root if args.root is not None else Path(__file__).resolve().parents[1]

    if args.command == "check":
        return cmd_check(args, root)
    if args.command == "evals":
        return cmd_evals(root)
    if args.command == "coverage":
        return cmd_coverage(root)
    if args.command == "status":
        return cmd_status(root)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
