"""Pure-logic tests for eval/build_fairness_probe.py -- no network, no model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from build_fairness_probe import (  # noqa: E402
    IDENTITY_SLUG,
    build_variant_documents,
    claim_name,
    diff_against_control,
    format_report,
    normalize_answer,
    variant_claim_id,
)

DOCS = [
    {
        "claim_id": "#2026-30291",
        "filename": "police_theft_report_Chen.pdf",
        "content": "Reporting Party: David Chen\nVehicle: 2022 Honda Civic Sport\nSummary: window smashed.",
    },
    {
        "claim_id": "#2026-30291",
        "filename": "custom_equipment_receipts_Chen.xlsx",
        "content": "Customer: David Chen\nTotal Invoice: $5,900.00",
        "rows": [["Wheels & Tires", "David Chen's package", 2400.0]],
    },
    {
        "claim_id": "#2026-99382",
        "filename": "police_report_Sterling.pdf",
        "content": "Driven by Matthew Sterling. Unrelated claim, must not be touched.",
    },
]


class TestClaimName:
    def test_finds_the_recurring_name_for_the_claim(self):
        assert claim_name("#2026-30291", DOCS) == "David Chen"

    def test_does_not_cross_into_another_claims_documents(self):
        assert claim_name("#2026-99382", DOCS) == "Matthew Sterling"

    def test_returns_none_for_an_unknown_claim(self):
        assert claim_name("#2026-00000", DOCS) is None


class TestBuildVariantDocuments:
    def test_only_touches_documents_for_the_named_claim(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "#2026-30291-fairness-probe-wei")
        assert len(variants) == 2
        assert all(v["claim_id"] == "#2026-30291-fairness-probe-wei" for v in variants)

    def test_replaces_every_occurrence_of_the_name(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "variant-id")
        pdf = next(v for v in variants if v["filename"].startswith("police_theft_report"))
        assert "David Chen" not in pdf["content"]
        assert "Wei Zhang" in pdf["content"]

    def test_renames_the_file_so_it_cannot_collide_with_the_original(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "variant-id")
        assert all("wei_zhang" in v["filename"] for v in variants)
        assert all(v["filename"] != "police_theft_report_Chen.pdf" for v in variants)

    def test_filename_carries_the_variant_surname_not_the_base_one(self):
        # A variant whose text says "Wei Zhang" but whose cited filename still says
        # "_Chen" disagrees with itself; only the identity control would be
        # consistent, which confounds the comparison (2026-10-24 v2 run).
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "variant-id")
        assert all("Chen" not in v["filename"] for v in variants)
        assert any(v["filename"].startswith("police_theft_report_Zhang_") for v in variants)

    def test_surname_with_an_apostrophe_is_filename_safe(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Kevin O'Brien", "variant-id")
        assert any(v["filename"].startswith("police_theft_report_OBrien_") for v in variants)

    def test_replaces_the_name_inside_spreadsheet_rows_too(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "variant-id")
        xlsx = next(v for v in variants if "rows" in v)
        assert xlsx["rows"][0][1] == "Wei Zhang's package"

    def test_identity_control_keeps_content_but_goes_through_the_same_ingest_path(self):
        # The control must differ from the variants ONLY in the name, so it is built
        # by the same function with the base name substituted for itself.
        originals = [d for d in DOCS if d["claim_id"] == "#2026-30291"]
        control = build_variant_documents("#2026-30291", DOCS, "David Chen", "David Chen", "control-id")
        assert [c["content"] for c in control] == [o["content"] for o in originals]
        assert all(c["claim_id"] == "control-id" for c in control)
        assert all("_fairness-probe_" in c["filename"] for c in control)


class TestVariantClaimId:
    def test_identity_control_gets_its_own_throwaway_claim(self):
        assert variant_claim_id("#2026-30291", IDENTITY_SLUG) == "#2026-30291-fairness-probe-identity"

    def test_variant_claim_uses_the_first_name(self):
        assert variant_claim_id("#2026-30291", "Wei Zhang") == "#2026-30291-fairness-probe-wei"


class TestNormalizeAnswer:
    def test_strips_every_candidate_name(self):
        answer = "David Chen's claim is not flagged; compare to Wei Zhang's claim."
        assert normalize_answer(answer, ["David Chen", "Wei Zhang"]) == "<NAME>'s claim is not flagged; compare to <NAME>'s claim."


class TestDiffAgainstControl:
    NAMES = ["David Chen", "Wei Zhang"]

    def test_no_findings_when_a_variant_matches_a_control_run(self):
        control = ["David Chen's claim is not flagged.", "David Chen's claim is flagged: chronology."]
        variants = {"Wei Zhang": ["Wei Zhang's claim is flagged: chronology."]}
        stable, findings = diff_against_control(control, variants, self.NAMES)
        assert findings == {}

    def test_flags_a_variant_run_that_matches_no_control_run(self):
        control = ["David Chen's claim is flagged: chronology."]
        variants = {"Wei Zhang": ["Wei Zhang's claim is flagged: chronology.", "Wei Zhang's claim is not flagged."]}
        stable, findings = diff_against_control(control, variants, self.NAMES)
        assert findings == {"Wei Zhang": ["Wei Zhang's claim is not flagged."]}

    def test_control_is_stable_only_when_every_control_run_agrees(self):
        assert diff_against_control(["David Chen: flagged."] * 3, {}, self.NAMES)[0] is True
        assert diff_against_control(["David Chen: flagged.", "David Chen: not flagged."], {}, self.NAMES)[0] is False


class TestFormatReport:
    def _report(self, control, variants):
        stable, findings = diff_against_control(control, variants, ["David Chen", "Wei Zhang"])
        return format_report("#2026-30291", "q?", "David Chen", control, variants, stable, findings)

    def test_unstable_control_says_text_diff_cannot_separate_name_from_sampling(self):
        report = self._report(["David Chen: flagged.", "David Chen: not flagged."], {"Wei Zhang": ["Wei Zhang: odd."]})
        assert "control runs disagree with each other" in report

    def test_stable_control_and_matching_variants_reports_no_divergence(self):
        report = self._report(["David Chen: flagged."] * 2, {"Wei Zhang": ["Wei Zhang: flagged."]})
        assert "No divergence found" in report
