from __future__ import annotations

import json
import re
import subprocess
import sys
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


def _venv_tool(name: str) -> str:
    """Resolves a console-script tool to the running interpreter's venv bin
    directory when present, falling back to PATH lookup."""
    venv_bin = Path(sys.executable).parent
    candidate = venv_bin / name
    return str(candidate) if candidate.exists() else name


def check_lint(root: Path) -> list[Finding]:
    """Runs `ruff check` over the repo. Blocking on any rule violation so the
    foundation gate enforces the same lint gate CI runs."""
    try:
        proc = subprocess.run(
            [_venv_tool("ruff"), "check", "."],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return [Finding("lint", "error", True, "ruff is not installed (add it to requirements.txt)", ())]
    if proc.returncode == 0:
        return []
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()] or ["ruff check failed"]
    return [Finding("lint", "error", True, "ruff check found violations", tuple(lines[:3]))]


# Reviewed dependency-audit exceptions: known vulnerabilities with no upstream
# fix, where the vulnerable code path was traced by hand and confirmed
# unreachable from this app's actual usage (not just "no fix yet, ignore it").
# Findings are still reported (never silently suppressed) so the gate output
# stays a complete audit trail; the annotation is the evidence the F8 matrix
# calls for. Re-review whenever requirements.txt's ragas/diskcache pins move,
# or a fix_versions entry appears for either id. See docs/enterprise-migration.md
# deferred findings for the full review record.
_DEPENDENCY_AUDIT_REVIEWED_EXCEPTIONS = {
    "PYSEC-2026-3046": (
        "reviewed 2026-09-02: SSRF is in ragas.metrics.collections."
        "multi_modal_faithfulness.util (_try_process_local_file/_try_process_url); "
        "eval/run_eval.py only imports ContextPrecision/ContextRecall/Faithfulness/"
        "FactualCorrectness from ragas.metrics.collections -- the multi-modal module "
        "is never imported, so the vulnerable functions are unreachable"
    ),
    "PYSEC-2026-2447": (
        "reviewed 2026-09-02: pickle RCE requires ragas.cache.DiskCacheBackend to be "
        "explicitly constructed with a cache_dir; grepped eval/run_eval.py and "
        "eval/ragas_lm_studio.py -- neither constructs it, so diskcache is an "
        "installed-but-never-invoked transitive dependency"
    ),
}


def check_dependency_audit(root: Path) -> list[Finding]:
    """Runs `pip-audit` against requirements.txt (F8: P0 dependency vulnerability
    audit). pip-audit's default (PyPI Advisory DB) JSON output carries no
    severity field, so there is no reliable signal to tier findings into
    critical/high vs low/medium — every finding is reported as advisory
    (non-blocking) until a severity source is wired in. Findings with no
    upstream fix and a hand-verified unreachable code path are annotated as
    reviewed exceptions (_DEPENDENCY_AUDIT_REVIEWED_EXCEPTIONS) rather than
    silently ignored. See the deferred findings in docs/enterprise-migration.md."""
    requirements = root / "requirements.txt"
    if not requirements.exists():
        return []
    try:
        proc = subprocess.run(
            [_venv_tool("pip-audit"), "-r", str(requirements), "--format", "json", "--progress-spinner", "off"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return [Finding("dependency-audit", "warning", False, "pip-audit is not installed (add it to requirements.txt)", ())]
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()] or ["pip-audit produced no parseable output"]
        return [Finding("dependency-audit", "error", True, "pip-audit failed to run", tuple(lines[:3]))]
    findings: list[Finding] = []
    for dependency in report.get("dependencies", []):
        name = dependency.get("name", "unknown")
        version = dependency.get("version", "unknown")
        for vuln in dependency.get("vulns", []):
            vuln_id = vuln.get("id", "unknown")
            fix_versions = vuln.get("fix_versions") or []
            fix = f"fix: {', '.join(fix_versions)}" if fix_versions else "no fix published yet"
            exception = _DEPENDENCY_AUDIT_REVIEWED_EXCEPTIONS.get(vuln_id)
            suffix = f"; {exception}" if exception else ""
            findings.append(
                Finding(
                    "dependency-audit",
                    "warning",
                    False,
                    f"{name}=={version} has known vulnerability {vuln_id} ({fix}){suffix}",
                    (vuln_id,),
                )
            )
    return findings


def check_static_security(root: Path) -> list[Finding]:
    """Runs `bandit` over the application code (F8: P1 static security analysis).
    Blocking only on HIGH severity + HIGH confidence findings — bandit's most
    conservative combination, treated as the equivalent of the matrix's
    "high-confidence critical findings"; everything else is advisory."""
    targets = [d for d in ("backend", "scripts", "eval") if (root / d).is_dir()]
    if not targets:
        return []
    try:
        proc = subprocess.run(
            [_venv_tool("bandit"), "-r", *targets, "-f", "json", "-q"],
            cwd=str(root),
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return [Finding("static-security", "error", False, "bandit is not installed (add it to requirements.txt)", ())]
    try:
        report = json.loads(proc.stdout)
    except (json.JSONDecodeError, ValueError):
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()] or ["bandit produced no parseable output"]
        return [Finding("static-security", "error", True, "bandit failed to run", tuple(lines[:3]))]
    findings: list[Finding] = []
    for result in report.get("results", []):
        severity = result.get("issue_severity", "UNDEFINED")
        confidence = result.get("issue_confidence", "UNDEFINED")
        blocking = severity == "HIGH" and confidence == "HIGH"
        location = f"{result.get('filename')}:{result.get('line_number')}"
        findings.append(
            Finding(
                "static-security",
                "error" if blocking else "warning",
                blocking,
                f"{result.get('test_id')} {result.get('issue_text', '')} (severity={severity}, confidence={confidence})",
                (location,),
            )
        )
    return findings


def run_gate(name: str, root: Path) -> list[Finding]:
    scanners = {
        "secrets": scan_secrets,
        "specs": check_required_specs,
        "docs": check_documentation,
        "lint": check_lint,
        "dependency-audit": check_dependency_audit,
        "static-security": check_static_security,
    }
    try:
        scanner = scanners[name]
    except KeyError as exc:
        raise ValueError(f"unknown gate: {name}") from exc
    return scanner(root)
