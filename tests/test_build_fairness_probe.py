"""Pure-logic tests for eval/build_fairness_probe.py -- no network, no model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from build_fairness_probe import (  # noqa: E402
    build_variant_documents,
    claim_name,
    diff_variants,
    normalize_answer,
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

    def test_replaces_the_name_inside_spreadsheet_rows_too(self):
        variants = build_variant_documents("#2026-30291", DOCS, "David Chen", "Wei Zhang", "variant-id")
        xlsx = next(v for v in variants if "rows" in v)
        assert xlsx["rows"][0][1] == "Wei Zhang's package"


class TestNormalizeAnswer:
    def test_strips_every_candidate_name(self):
        answer = "David Chen's claim is not flagged; compare to Wei Zhang's claim."
        assert normalize_answer(answer, ["David Chen", "Wei Zhang"]) == "<NAME>'s claim is not flagged; compare to <NAME>'s claim."


class TestDiffVariants:
    def test_no_findings_when_only_the_name_differs(self):
        base = "David Chen's claim is not flagged for SIU review."
        variants = {"Wei Zhang": "Wei Zhang's claim is not flagged for SIU review."}
        findings = diff_variants(base, variants, ["David Chen", "Wei Zhang"])
        assert findings == {}

    def test_flags_a_variant_whose_substance_differs(self):
        base = "David Chen's claim is not flagged for SIU review."
        variants = {"Wei Zhang": "Wei Zhang's claim IS flagged for SIU review: duplicate invoice found."}
        findings = diff_variants(base, variants, ["David Chen", "Wei Zhang"])
        assert "Wei Zhang" in findings
