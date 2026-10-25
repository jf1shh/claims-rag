"""Pure-logic tests for eval/timed_study -- no network, no model, no real input()/time.time()."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "eval"))

from golden_queries import GOLDEN_QUERIES  # noqa: E402
from timed_study import question_sets  # noqa: E402
from timed_study.runner import (  # noqa: E402
    CSV_COLUMNS,
    main,
    run_question,
    write_csv_row,
)

GOLDEN_IDS = {q["id"] for q in GOLDEN_QUERIES}


class TestQuestionSets:
    def test_each_set_has_ten_questions(self):
        assert len(question_sets.SET_A) == 10
        assert len(question_sets.SET_B) == 10

    def test_sets_are_disjoint(self):
        ids_a = {q["id"] for q in question_sets.SET_A}
        ids_b = {q["id"] for q in question_sets.SET_B}
        assert ids_a.isdisjoint(ids_b)

    def test_every_id_traces_back_to_a_real_golden_query(self):
        for query in question_sets.SET_A + question_sets.SET_B:
            assert query["id"] in GOLDEN_IDS
            assert query is next(q for q in GOLDEN_QUERIES if q["id"] == query["id"])

    def test_difficulty_balance_report_matches_between_sets(self):
        report = question_sets.difficulty_balance_report()
        # Tolerance is 0 (exact match): selection quotas in question_sets.py are
        # chosen specifically so each category splits evenly 1:1 between A and B,
        # so any difference would mean the split logic regressed, not natural
        # sampling noise.
        assert report["A"] == report["B"]
        assert sum(report["A"].values()) == 10
        assert sum(report["B"].values()) == 10

    def test_selection_is_deterministic_across_rebuilds(self):
        set_a_ids, set_b_ids = question_sets._build_sets()
        assert set_a_ids == [q["id"] for q in question_sets.SET_A]
        assert set_b_ids == [q["id"] for q in question_sets.SET_B]
        # Calling the builder again (simulating a re-import) must reproduce
        # the exact same split.
        set_a_ids_2, set_b_ids_2 = question_sets._build_sets()
        assert set_a_ids_2 == set_a_ids
        assert set_b_ids_2 == set_b_ids


class TestRunQuestion:
    QUESTION = {"id": "q1", "query": "What is covered?"}

    def test_happy_path_computes_elapsed_seconds(self):
        row = run_question(
            self.QUESTION,
            start_fn=lambda: 100.0,
            stop_fn=lambda: 142.5,
            answer_fn=lambda: "It is covered.",
            method="manual",
            participant="jared",
        )
        assert row == {
            "question_id": "q1",
            "method": "manual",
            "participant": "jared",
            "started_at": 100.0,
            "stopped_at": 142.5,
            "elapsed_seconds": 42.5,
            "answer_text": "It is covered.",
        }

    def test_stop_before_start_raises_value_error(self):
        try:
            run_question(
                self.QUESTION,
                start_fn=lambda: 100.0,
                stop_fn=lambda: 99.0,
                answer_fn=lambda: "answer",
                method="manual",
                participant="jared",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for stop < start")


class TestWriteCsvRow:
    ROW = {
        "question_id": "q1",
        "method": "manual",
        "participant": "jared",
        "started_at": 1.0,
        "stopped_at": 2.0,
        "elapsed_seconds": 1.0,
        "answer_text": "plain answer",
    }

    def test_header_written_once_on_fresh_file(self, tmp_path):
        path = tmp_path / "results.csv"
        write_csv_row(path, self.ROW)
        write_csv_row(path, {**self.ROW, "question_id": "q2"})
        lines = path.read_text().splitlines()
        assert lines[0] == ",".join(CSV_COLUMNS)
        assert len(lines) == 3  # header + 2 rows
        assert lines[0].count("question_id") == 1

    def test_column_order_matches_spec(self, tmp_path):
        path = tmp_path / "results.csv"
        write_csv_row(path, self.ROW)
        with path.open() as f:
            reader = csv.reader(f)
            header = next(reader)
        assert header == CSV_COLUMNS

    def test_answer_text_with_comma_and_newline_round_trips(self, tmp_path):
        path = tmp_path / "results.csv"
        tricky_row = {**self.ROW, "answer_text": "line one, with a comma\nline two"}
        write_csv_row(path, tricky_row)
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            read_back = next(reader)
        assert read_back["answer_text"] == "line one, with a comma\nline two"


class TestMainEndToEnd:
    def test_session_writes_csv_and_prints_median(self, tmp_path, monkeypatch, capsys):
        out_path = tmp_path / "session.csv"

        # Two Enter-then-answer pairs per question (press Enter, type answer).
        # Fixed fake timestamps make elapsed_seconds, and therefore the median,
        # an exact, assertable value rather than real wall-clock noise.
        times = iter([10.0, 20.0, 30.0, 50.0, 100.0, 130.0])
        inputs = iter(["", "a1", "", "a2", "", "a3"])

        from timed_study import runner

        monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))
        monkeypatch.setattr(runner.time, "time", lambda: next(times))
        monkeypatch.setattr(runner, "SET_A", question_sets.SET_A[:3])

        main(
            [
                "--participant",
                "jared",
                "--method",
                "manual",
                "--set",
                "A",
                "--out",
                str(out_path),
            ]
        )

        with out_path.open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3
        assert list(rows[0].keys()) == CSV_COLUMNS
        assert [r["elapsed_seconds"] for r in rows] == ["10.0", "20.0", "30.0"]

        out = capsys.readouterr().out
        assert "median elapsed_seconds=20.0" in out
