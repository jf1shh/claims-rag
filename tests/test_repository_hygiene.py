from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_given_icm_navigation_files_then_each_points_to_authoritative_artifacts():
    context = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    assert "docs/superpowers/specs/2026-08-28-enterprise-foundation-design.md" in context
    assert "stages/verify/CONTEXT.md" in context


def test_given_all_icm_stages_then_each_has_required_contract_sections():
    for stage in ("sense", "propose", "act", "verify", "learn"):
        text = (ROOT / "stages" / stage / "CONTEXT.md").read_text(encoding="utf-8")
        for heading in ("Inputs", "Process", "Checkpoints", "Audit", "Outputs"):
            assert heading in text
