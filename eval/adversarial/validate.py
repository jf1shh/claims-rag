"""Structural validator for eval/adversarial/cases.py (contract: docs/agent-work/adversarial-eval/SPEC.md).

Checks shape only -- not whether a case is a good attack. Standard library only.

Run:  .venv/bin/python -B eval/adversarial/validate.py
"""
from __future__ import annotations

import os
import sys

FILE_TYPES = {"txt", "docx", "pdf"}
CATEGORIES = {"injection", "conflict"}
STATUSES = {"grounded", "insufficient_evidence", "conflicting_evidence", "error"}
KNOWN_CLAIM_IDS = {"#2026-10492", "#2026-30291", "#2026-55912", "#2026-99382"}


def _str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def validate(fixtures: list[dict], cases: list[dict]) -> list[str]:
    """Return a list of human-readable problems; empty means the data matches the contract."""
    problems: list[str] = []
    fixture_names: set[str] = set()

    for index, fx in enumerate(fixtures):
        where = f"fixture[{index}] {fx.get('filename', '?')!r}"
        name = fx.get("filename")
        if not isinstance(name, str) or not name.startswith("ADV_"):
            problems.append(f"{where}: filename must be a str starting with 'ADV_'")
            continue
        if name in fixture_names:
            problems.append(f"{where}: duplicate filename")
        fixture_names.add(name)
        file_type = fx.get("file_type")
        if file_type not in FILE_TYPES:
            problems.append(f"{where}: file_type must be one of {sorted(FILE_TYPES)}")
        elif not name.lower().endswith("." + file_type):
            problems.append(f"{where}: extension does not match file_type {file_type!r}")
        if fx.get("claim_id") is not None and fx.get("claim_id") not in KNOWN_CLAIM_IDS:
            problems.append(f"{where}: claim_id must be None or one of {sorted(KNOWN_CLAIM_IDS)}")
        if not isinstance(fx.get("title"), str) or not fx.get("title"):
            problems.append(f"{where}: title must be a non-empty str")
        sections = fx.get("sections")
        if not isinstance(sections, list) or not sections or not all(
            isinstance(s, tuple) and len(s) == 2 and all(isinstance(p, str) and p for p in s) for s in sections
        ):
            problems.append(f"{where}: sections must be a non-empty list of (heading, body) str tuples")

    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        where = f"case[{index}] {case.get('id', '?')!r}"
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            problems.append(f"{where}: id must be a non-empty str")
        elif case_id in case_ids:
            problems.append(f"{where}: duplicate id")
        else:
            case_ids.add(case_id)
        if case.get("category") not in CATEGORIES:
            problems.append(f"{where}: category must be one of {sorted(CATEGORIES)}")
        for key in ("subtype", "query", "rationale"):
            if not isinstance(case.get(key), str) or not case.get(key):
                problems.append(f"{where}: {key} must be a non-empty str")
        if case.get("claim_id") is not None and case.get("claim_id") not in KNOWN_CLAIM_IDS:
            problems.append(f"{where}: claim_id must be None or one of {sorted(KNOWN_CLAIM_IDS)}")
        if not _str_list(case.get("fixtures")) or not case.get("fixtures"):
            problems.append(f"{where}: fixtures must be a non-empty list of filenames")
        else:
            for name in case["fixtures"]:
                if name not in fixture_names:
                    problems.append(f"{where}: fixture {name!r} is not defined in ADVERSARIAL_FIXTURES")
        for key in ("must_not_contain", "must_cite"):
            if not isinstance(case.get(key), list) or (case.get(key) and not _str_list(case.get(key))):
                problems.append(f"{where}: {key} must be a list of non-empty str")
        groups = case.get("must_contain_groups")
        if not isinstance(groups, list) or not all(_str_list(g) and g for g in groups):
            problems.append(f"{where}: must_contain_groups must be a list of non-empty str lists")
        status = case.get("expected_status")
        if status is not None and (not _str_list(status) or not set(status) <= STATUSES):
            problems.append(f"{where}: expected_status must be None or a list drawn from {sorted(STATUSES)}")
        judge = case.get("judge_question")
        if judge is not None and (not isinstance(judge, str) or not judge):
            problems.append(f"{where}: judge_question must be None or a non-empty str")
        if case.get("category") == "injection" and not case.get("must_not_contain"):
            problems.append(f"{where}: injection cases need at least one must_not_contain marker")
        if case.get("category") == "conflict" and not (case.get("must_contain_groups") or judge):
            problems.append(f"{where}: conflict cases need must_contain_groups or a judge_question")

    return problems


def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cases import ADVERSARIAL_CASES, ADVERSARIAL_FIXTURES

    problems = validate(ADVERSARIAL_FIXTURES, ADVERSARIAL_CASES)
    for problem in problems:
        print(f"FAIL {problem}")
    categories = sorted({c.get("category") for c in ADVERSARIAL_CASES}, key=str)
    counts = {cat: sum(1 for c in ADVERSARIAL_CASES if c.get("category") == cat) for cat in categories}
    print(f"{len(ADVERSARIAL_FIXTURES)} fixtures, {len(ADVERSARIAL_CASES)} cases {counts}: "
          f"{'OK' if not problems else f'{len(problems)} problem(s)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
