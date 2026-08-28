from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.harness import Finding, run_gate


GATES = ("secrets", "specs", "docs")


def format_finding(finding: Finding) -> str:
    evidence = ", ".join(finding.evidence)
    return f"[{finding.severity}] {finding.rule_id}: {finding.message} ({evidence})"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic AutoClaimsRAG foundation gates")
    parser.add_argument("--mode", choices=("advisory", "gate"), default="advisory")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    for name in GATES:
        findings.extend(run_gate(name, args.root))
    for finding in findings:
        print(format_finding(finding))
    blocking = [finding for finding in findings if finding.blocking]
    if args.mode == "gate" and blocking:
        print(f"foundation gate failed: {len(blocking)} blocking finding(s)", file=sys.stderr)
        return 1
    print(f"foundation gate passed: {len(findings)} finding(s), {len(blocking)} blocking")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
