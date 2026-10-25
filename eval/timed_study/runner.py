"""Handler timed-study session runner (plan.md, "b. Timed study").

Times how long a participant takes to answer each question in SET_A or SET_B,
by manual lookup or with ClaimsRAG, and appends one CSV row per question.
Pure timing/CSV logic (`run_question`, `write_csv_row`) is separated from the
interactive CLI (`main`) so it's testable without real `input()`/`time.time()`.
"""
import argparse
import csv
import statistics
import time
from pathlib import Path
from typing import Callable

from .question_sets import SET_A, SET_B

CSV_COLUMNS = [
    "question_id",
    "method",
    "participant",
    "started_at",
    "stopped_at",
    "elapsed_seconds",
    "answer_text",
]


def run_question(
    question: dict,
    start_fn: Callable[[], float],
    stop_fn: Callable[[], float],
    answer_fn: Callable[[], str],
    method: str,
    participant: str,
) -> dict:
    started_at = start_fn()
    answer_text = answer_fn()
    stopped_at = stop_fn()
    if stopped_at < started_at:
        raise ValueError(
            f"stop ({stopped_at}) is before start ({started_at}) for question "
            f"{question['id']!r} -- caller passed a bad start_fn/stop_fn pair"
        )
    return {
        "question_id": question["id"],
        "method": method,
        "participant": participant,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "elapsed_seconds": stopped_at - started_at,
        "answer_text": answer_text,
    }


def write_csv_row(path: Path, row: dict, write_header_if_new: bool = True) -> None:
    path = Path(path)
    file_is_new_or_empty = write_header_if_new and (
        not path.exists() or path.stat().st_size == 0
    )
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if file_is_new_or_empty:
            writer.writeheader()
        writer.writerow(row)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run one timed-study session.")
    parser.add_argument("--participant", required=True)
    parser.add_argument("--method", required=True, choices=["manual", "claimsrag"])
    parser.add_argument("--set", required=True, choices=["A", "B"])
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    questions = SET_A if args.set == "A" else SET_B
    out_path = Path(args.out)

    elapsed_seconds = []
    for question in questions:
        print(f"[{question['id']}] {question['query']}")
        input("Press Enter to start the timer... ")
        row = run_question(
            question,
            start_fn=time.time,
            stop_fn=time.time,
            answer_fn=lambda: input("Answer: "),
            method=args.method,
            participant=args.participant,
        )
        write_csv_row(out_path, row)
        elapsed_seconds.append(row["elapsed_seconds"])

    median = statistics.median(elapsed_seconds)
    print(f"Session complete: {len(questions)} questions, median elapsed_seconds={median}")


if __name__ == "__main__":
    main()
