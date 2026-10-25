"""Pure-logic tests for eval/agreement.py -- no network, no model."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from agreement import (  # noqa: E402
    agreement_report,
    fleiss_kappa,
    format_markdown_report,
    main,
    parse_packet,
    pct_exact,
    pct_within_one,
    weighted_cohen_kappa,
)

PACKET_A = """\
# fake packet

## question-one

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ yes

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 4

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ nothing much

---

## question-two

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ five

**What would a handler catch that the automated judge wouldn't?:** _Notes:_

---

## question-three

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ with caveats

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 3

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ some free text here
"""


def write_packet(path, text):
    path.write_text(text)
    return path


class TestParsePacket:
    def test_given_fully_scored_question_when_parsed_then_fields_match(self, tmp_path):
        packet = write_packet(tmp_path / "a.md", PACKET_A)
        result = parse_packet(packet)
        assert result["question-one"] == {
            "verdict": "yes",
            "practice_score": 4,
            "notes": "nothing much",
        }

    def test_given_blank_score_when_parsed_then_field_is_none(self, tmp_path):
        packet = write_packet(tmp_path / "a.md", PACKET_A)
        result = parse_packet(packet)
        assert result["question-two"]["verdict"] is None
        assert result["question-two"]["notes"] == ""

    def test_given_malformed_practice_score_when_parsed_then_field_is_none_not_raise(self, tmp_path):
        packet = write_packet(tmp_path / "a.md", PACKET_A)
        result = parse_packet(packet)
        assert result["question-two"]["practice_score"] is None

    def test_given_well_formed_question_when_parsed_then_all_fields_present(self, tmp_path):
        packet = write_packet(tmp_path / "a.md", PACKET_A)
        result = parse_packet(packet)
        assert result["question-three"] == {
            "verdict": "with caveats",
            "practice_score": 3,
            "notes": "some free text here",
        }


class TestWeightedCohenKappa:
    def test_given_perfect_agreement_when_scored_then_kappa_is_one(self):
        a = [0, 1, 2, 0, 1, 2]
        b = [0, 1, 2, 0, 1, 2]
        assert weighted_cohen_kappa(a, b, max_level=2) == 1.0

    def test_given_identical_constant_ratings_when_scored_then_kappa_is_one_not_error(self):
        a = [1, 1, 1, 1]
        b = [1, 1, 1, 1]
        assert weighted_cohen_kappa(a, b, max_level=4) == 1.0

    def test_given_known_partial_agreement_when_scored_then_matches_hand_computed_value(self):
        # a and b over categories {0,1,2}, max_level=2.
        # pairs: (0,0) (1,1) (2,0) (1,2) (0,0) (2,2)
        a = [0, 1, 2, 1, 0, 2]
        b = [0, 1, 0, 2, 0, 2]
        # weights w(i,j) = |i-j|/2 -> w(2,0)=1, w(1,2)=0.5, rest matching pairs w=0
        # observed weighted disagreement: (1 + 0.5) / 6 = 0.25
        # marginals a: {0:2, 1:2, 2:2}; b: {0:3, 1:1, 2:2}
        # pe = sum_i sum_j a_count[i]*b_count[j]*w(i,j) / 36
        #   i=0: j=0 w0 ... (0*anything contributes 0 since w(0,0)=0, w(0,1)=0.5, w(0,2)=1)
        #     a0=2: j=1 -> 2*1*0.5=1.0; j=2 -> 2*2*1=4.0
        #   i=1: a1=2: j=0 -> 2*3*0.5=3.0; j=2 -> 2*2*0.5=2.0
        #   i=2: a2=2: j=0 -> 2*3*1=6.0; j=1 -> 2*1*0.5=1.0
        #   total = 1+4+3+2+6+1 = 17.0; pe = 17/36
        # kappa = 1 - po/pe = 1 - 0.25/(17/36) = 1 - (0.25*36/17) = 1 - 9/17 = 8/17
        expected = 1 - (0.25) / (17 / 36)
        result = weighted_cohen_kappa(a, b, max_level=2)
        assert abs(result - expected) < 1e-9
        assert abs(result - 8 / 17) < 1e-9


class TestFleissKappa:
    def test_given_perfect_agreement_when_scored_then_kappa_is_one(self):
        ratings = [[0, 0, 0], [1, 1, 1], [2, 2, 2], [0, 0, 0]]
        assert fleiss_kappa(ratings, n_categories=3) == 1.0

    def test_given_known_partial_case_when_scored_then_matches_hand_computed_value(self):
        # 3 raters, 2 items, 2 categories.
        # item 1: [0,0,1] -> n_i0=2, n_i1=1 -> P_1 = (4+1-3)/(3*2) = 2/6 = 1/3
        # item 2: [1,1,1] -> n_i0=0, n_i1=3 -> P_2 = (0+9-3)/6 = 6/6 = 1
        # P_bar = (1/3 + 1) / 2 = 2/3
        # total assignments per category: cat0: 2, cat1: 4 (over N*n=6)
        # p0 = 2/6 = 1/3, p1 = 4/6 = 2/3
        # P_e = (1/3)^2 + (2/3)^2 = 1/9 + 4/9 = 5/9
        # kappa = (P_bar - P_e) / (1 - P_e) = (2/3 - 5/9) / (1 - 5/9) = (1/9) / (4/9) = 1/4
        ratings = [[0, 0, 1], [1, 1, 1]]
        result = fleiss_kappa(ratings, n_categories=2)
        assert abs(result - 0.25) < 1e-9


class TestPctMetrics:
    def test_pct_exact_straightforward(self):
        assert pct_exact([1, 2, 3], [1, 2, 4]) == 2 / 3

    def test_pct_within_one_straightforward(self):
        assert pct_within_one([1, 2, 3], [1, 3, 5]) == 2 / 3

    def test_pct_exact_pairwise_average_for_three_raters(self):
        a, b, c = [1, 1], [1, 2], [2, 2]
        pairs = [pct_exact(a, b), pct_exact(a, c), pct_exact(b, c)]
        assert sum(pairs) / len(pairs) == (0.5 + 0.0 + 0.5) / 3


ALICE_PACKET = """\
## q1

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ yes

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 5

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ fine

---

## q2

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ no

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 2

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ missed a caveat

---

## q3

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ with caveats

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_

**What would a handler catch that the automated judge wouldn't?:** _Notes:_
"""

BOB_PACKET = """\
## q1

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ yes

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 4

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ fine too

---

## q2

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ with caveats

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 3

**What would a handler catch that the automated judge wouldn't?:** _Notes:_ disagrees with alice

---

## q3

**Would a claims handler act on this answer as written? (yes / with caveats / no):** _Score:_ with caveats

**Does it match real claims-handling practice, beyond mere faithfulness to the source text? (1-5):** _Score:_ 3

**What would a handler catch that the automated judge wouldn't?:** _Notes:_
"""


class TestAgreementReport:
    def test_given_mismatched_question_ids_when_reported_then_raises_value_error(self, tmp_path):
        alice = parse_packet(write_packet(tmp_path / "alice.md", ALICE_PACKET))
        bob_text = BOB_PACKET.replace("## q3", "## q3-renamed")
        bob = parse_packet(write_packet(tmp_path / "bob.md", bob_text))
        with pytest.raises(ValueError) as exc_info:
            agreement_report({"alice": alice, "bob": bob})
        msg = str(exc_info.value)
        assert "bob" in msg or "alice" in msg
        assert "q3" in msg

    def test_given_partially_scored_question_when_reported_then_excluded_and_counted(self, tmp_path):
        alice = parse_packet(write_packet(tmp_path / "alice.md", ALICE_PACKET))
        bob = parse_packet(write_packet(tmp_path / "bob.md", BOB_PACKET))
        report = agreement_report({"alice": alice, "bob": bob})
        # q3's practice_score is blank for alice -> excluded from that metric only.
        assert report["practice_score"]["n_items_scored"] == 2
        assert report["practice_score"]["n_excluded"] == 1
        assert report["verdict"]["n_items_scored"] == 3
        assert report["verdict"]["n_excluded"] == 0

    def test_given_disagreeing_raters_when_reported_then_disagreements_listed_and_sorted(self, tmp_path):
        alice = parse_packet(write_packet(tmp_path / "alice.md", ALICE_PACKET))
        bob = parse_packet(write_packet(tmp_path / "bob.md", BOB_PACKET))
        report = agreement_report({"alice": alice, "bob": bob})
        verdict_disagreements = report["verdict"]["disagreements"]
        qids = [d["question_id"] for d in verdict_disagreements]
        assert qids == sorted(qids)
        assert "q2" in qids
        q2 = next(d for d in verdict_disagreements if d["question_id"] == "q2")
        assert q2["values"] == {"alice": "no", "bob": "with caveats"}

    def test_given_two_raters_when_reported_then_kappa_present(self, tmp_path):
        alice = parse_packet(write_packet(tmp_path / "alice.md", ALICE_PACKET))
        bob = parse_packet(write_packet(tmp_path / "bob.md", BOB_PACKET))
        report = agreement_report({"alice": alice, "bob": bob})
        assert report["verdict"]["kappa"] is not None
        assert report["verdict"]["n_raters"] == 2


class TestFormatMarkdownReport:
    def test_contains_rater_names_synthetic_sentence_and_closing_disclaimer(self, tmp_path):
        alice = parse_packet(write_packet(tmp_path / "alice.md", ALICE_PACKET))
        bob = parse_packet(write_packet(tmp_path / "bob.md", BOB_PACKET))
        report = agreement_report({"alice": alice, "bob": bob})
        md = format_markdown_report(report, ["alice", "bob"])
        assert "alice" in md
        assert "bob" in md
        assert "synthetic" in md.lower()
        assert "Disagreements" in md
        assert "inter-rater agreement" in md.lower()
        assert "real-claim accuracy" in md.lower() or "real claim accuracy" in md.lower()


class TestMainCli:
    def test_given_two_packets_when_main_runs_then_report_file_written(self, tmp_path):
        alice_path = write_packet(tmp_path / "alice.md", ALICE_PACKET)
        bob_path = write_packet(tmp_path / "bob.md", BOB_PACKET)
        out_path = tmp_path / "agreement-report.md"
        exit_code = main([
            "--packet", f"alice={alice_path}",
            "--packet", f"bob={bob_path}",
            "--out", str(out_path),
        ])
        assert exit_code == 0
        assert out_path.exists()
        content = out_path.read_text()
        assert "alice" in content
        assert "bob" in content

    def test_given_fewer_than_two_packets_when_main_runs_then_exits_nonzero(self, tmp_path):
        alice_path = write_packet(tmp_path / "alice.md", ALICE_PACKET)
        out_path = tmp_path / "agreement-report.md"
        exit_code = main([
            "--packet", f"alice={alice_path}",
            "--out", str(out_path),
        ])
        assert exit_code == 1
        assert not out_path.exists()
