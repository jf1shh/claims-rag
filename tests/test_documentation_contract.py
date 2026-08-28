from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_given_public_installation_docs_when_checked_then_documented_commands_exist():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pytest tests/" in readme
    assert "/health/live" in readme
    assert "ICM workflow" in readme


def test_given_env_template_when_checked_then_required_foundation_settings_are_documented():
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for key in ("APP_ENV", "RAG_DB_PATH", "MAX_UPLOAD_BYTES", "MAX_QUERY_CHARS", "SIMULATION_MODE"):
        assert key in env


def test_given_release_checklist_when_checked_then_actual_verification_commands_are_documented():
    checklist = (ROOT / "docs" / "release-checklist.md").read_text(encoding="utf-8")
    assert "python -m pytest -q" in checklist
    assert "scripts/run_foundation_gates.py --mode gate" in checklist
    assert "eval/parity_runner.py" in checklist
