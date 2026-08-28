from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    blocking: bool
    message: str
    evidence: tuple[str, ...]


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(api[_-]?key|secret|token|password)\s*[:=]\s*['\"][^'\"]{12,}['\"]"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def scan_secrets(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    ignored = {".git", ".venv", "__pycache__", "stored_documents", "tests", "docs/superpowers"}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("tests/") or relative.startswith("docs/superpowers/"):
            continue
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if any(pattern.search(line) for pattern in _SECRET_PATTERNS):
                findings.append(Finding("secret-scan", "error", True, "possible credential in repository", (f"{path}:{line_number}",)))
    return findings


def check_required_specs(root: Path) -> list[Finding]:
    specs = list((root / "docs" / "superpowers" / "specs").glob("*-design.md"))
    if specs:
        return []
    return [Finding("spec-presence", "error", True, "no approved design specification found", ("docs/superpowers/specs",))]


def check_documentation(root: Path) -> list[Finding]:
    readme = root / "README.md"
    if not readme.exists():
        return [Finding("documentation", "warning", False, "README.md is missing", (str(readme),))]
    return []


def run_gate(name: str, root: Path) -> list[Finding]:
    scanners = {
        "secrets": scan_secrets,
        "specs": check_required_specs,
        "docs": check_documentation,
    }
    try:
        scanner = scanners[name]
    except KeyError as exc:
        raise ValueError(f"unknown gate: {name}") from exc
    return scanner(root)
