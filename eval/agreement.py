"""Inter-rater agreement scoring for the expert-review packet (governance gap 2,
docs/governance/expert-review-protocol.md). Reads 2+ filled-in copies of a packet
built by build_expert_review_packet.py and reports how well the raters agree.

Usage:
    .venv/bin/python eval/agreement.py \\
        --packet alice=eval/expert-review/2026-10-24-packet-alice.md \\
        --packet bob=eval/expert-review/2026-10-24-packet-bob.md \\
        --out eval/expert-review/agreement-report.md
"""
import argparse
import itertools
import re
import sys
from pathlib import Path

VERDICT_ORDER = {"no": 0, "with caveats": 1, "yes": 2}

_SECTION_RE = re.compile(r"^## (\S+)\s*$", re.MULTILINE)
_VERDICT_LINE_RE = re.compile(r"caveats / no\):\*\*\s*_Score:_\s*(.*)")
_PRACTICE_LINE_RE = re.compile(r"\(1-5\):\*\*\s*_Score:_\s*(.*)")
_NOTES_LINE_RE = re.compile(r"_Notes:_\s*(.*)")


def _parse_section(body):
    verdict = None
    practice_score = None
    notes = ""
    for line in body.splitlines():
        verdict_match = _VERDICT_LINE_RE.search(line)
        if verdict_match:
            raw = verdict_match.group(1).strip().lower()
            verdict = raw if raw in VERDICT_ORDER else None
            continue
        practice_match = _PRACTICE_LINE_RE.search(line)
        if practice_match:
            raw = practice_match.group(1).strip()
            try:
                value = int(raw)
            except ValueError:
                value = None
            practice_score = value if value is not None and 1 <= value <= 5 else None
            continue
        notes_match = _NOTES_LINE_RE.search(line)
        if notes_match:
            notes = notes_match.group(1).strip()
    return {"verdict": verdict, "practice_score": practice_score, "notes": notes}


def parse_packet(path: Path) -> dict:
    text = path.read_text()
    headers = list(_SECTION_RE.finditer(text))
    sections = {}
    for i, header in enumerate(headers):
        question_id = header.group(1)
        start = header.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        sections[question_id] = _parse_section(text[start:end])
    return sections


def weighted_cohen_kappa(a: list, b: list, max_level: int) -> float:
    n = len(a)

    def weight(i, j):
        return abs(i - j) / max_level if max_level else 0.0

    observed = sum(weight(x, y) for x, y in zip(a, b, strict=True)) / n

    a_counts = {}
    b_counts = {}
    for x in a:
        a_counts[x] = a_counts.get(x, 0) + 1
    for y in b:
        b_counts[y] = b_counts.get(y, 0) + 1

    levels = range(max_level + 1)
    expected = sum(
        a_counts.get(i, 0) * b_counts.get(j, 0) * weight(i, j) for i in levels for j in levels
    ) / (n * n)

    if expected == 0:
        return 1.0
    return 1 - observed / expected


def fleiss_kappa(ratings: list, n_categories: int) -> float:
    n_items = len(ratings)
    n_raters = len(ratings[0])

    category_counts = []
    for row in ratings:
        counts = [0] * n_categories
        for value in row:
            counts[value] += 1
        category_counts.append(counts)

    category_totals = [
        sum(category_counts[i][j] for i in range(n_items)) for j in range(n_categories)
    ]
    p_j = [total / (n_items * n_raters) for total in category_totals]

    per_item_agreement = [
        (sum(count * count for count in counts) - n_raters) / (n_raters * (n_raters - 1))
        for counts in category_counts
    ]
    mean_agreement = sum(per_item_agreement) / n_items
    expected_agreement = sum(p * p for p in p_j)

    if expected_agreement == 1:
        return 1.0
    return (mean_agreement - expected_agreement) / (1 - expected_agreement)


def pct_exact(a: list, b: list) -> float:
    return sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)


def pct_within_one(a: list, b: list) -> float:
    return sum(1 for x, y in zip(a, b, strict=True) if abs(x - y) <= 1) / len(a)


def _pairwise_average(values_by_rater: dict, rater_names: list, metric_fn) -> float:
    pairs = list(itertools.combinations(rater_names, 2))
    scores = [
        metric_fn(values_by_rater[r1], values_by_rater[r2]) for r1, r2 in pairs
    ]
    return sum(scores) / len(scores)


def _collect_metric(packets: dict, rater_names: list, question_ids: list, field: str):
    """Returns (included: {qid: {rater: value}}, n_excluded)."""
    included = {}
    excluded = 0
    for qid in question_ids:
        values = {}
        complete = True
        for rater in rater_names:
            value = packets[rater][qid][field]
            if value is None:
                complete = False
                break
            values[rater] = value
        if complete:
            included[qid] = values
        else:
            excluded += 1
    return included, excluded


def _score_metric(included: dict, rater_names: list, encode, max_level: int, n_categories: int) -> dict:
    n_raters = len(rater_names)
    n_items = len(included)
    disagreements = sorted(
        (
            {"question_id": qid, "values": dict(values)}
            for qid, values in included.items()
            if len(set(values.values())) > 1
        ),
        key=lambda d: d["question_id"],
    )

    if n_raters < 2 or n_items == 0:
        return {
            "n_raters": n_raters,
            "n_items_scored": n_items,
            "n_excluded": None,
            "kappa": None,
            "pct_exact": None,
            "pct_within_one": None,
            "disagreements": disagreements,
        }

    sorted_qids = sorted(included)
    encoded_by_rater = {
        rater: [encode(included[qid][rater]) for qid in sorted_qids] for rater in rater_names
    }

    if n_raters == 2:
        r1, r2 = rater_names
        kappa = weighted_cohen_kappa(encoded_by_rater[r1], encoded_by_rater[r2], max_level)
    else:
        ratings_matrix = [
            [encoded_by_rater[rater][i] for rater in rater_names] for i in range(n_items)
        ]
        kappa = fleiss_kappa(ratings_matrix, n_categories)

    return {
        "n_raters": n_raters,
        "n_items_scored": n_items,
        "n_excluded": None,
        "kappa": kappa,
        "pct_exact": _pairwise_average(encoded_by_rater, rater_names, pct_exact),
        "pct_within_one": _pairwise_average(encoded_by_rater, rater_names, pct_within_one),
        "disagreements": disagreements,
    }


def agreement_report(packets: dict) -> dict:
    rater_names = list(packets.keys())
    question_ids_by_rater = {rater: set(data.keys()) for rater, data in packets.items()}
    reference_rater = rater_names[0]
    reference_ids = question_ids_by_rater[reference_rater]
    for rater in rater_names[1:]:
        ids = question_ids_by_rater[rater]
        if ids != reference_ids:
            missing_from_rater = reference_ids - ids
            missing_from_reference = ids - reference_ids
            if missing_from_rater:
                qid = sorted(missing_from_rater)[0]
                raise ValueError(
                    f"question id '{qid}' is in {reference_rater}'s packet but missing from {rater}'s"
                )
            qid = sorted(missing_from_reference)[0]
            raise ValueError(
                f"question id '{qid}' is in {rater}'s packet but missing from {reference_rater}'s"
            )

    question_ids = sorted(reference_ids)

    verdict_included, verdict_excluded = _collect_metric(packets, rater_names, question_ids, "verdict")
    practice_included, practice_excluded = _collect_metric(
        packets, rater_names, question_ids, "practice_score"
    )

    verdict_report = _score_metric(
        verdict_included, rater_names, lambda v: VERDICT_ORDER[v], max_level=2, n_categories=3
    )
    verdict_report["n_excluded"] = verdict_excluded

    practice_report = _score_metric(
        practice_included, rater_names, lambda v: v - 1, max_level=4, n_categories=5
    )
    practice_report["n_excluded"] = practice_excluded

    return {"verdict": verdict_report, "practice_score": practice_report}


def _format_metric_section(title, metric):
    lines = [f"### {title}", ""]
    lines.append(
        f"- Raters: {metric['n_raters']}; questions scored by all raters: "
        f"{metric['n_items_scored']} ({metric['n_excluded']} excluded as not yet fully scored)"
    )
    kappa_str = f"{metric['kappa']:.3f}" if metric["kappa"] is not None else "n/a"
    pct_exact_str = f"{metric['pct_exact']:.3f}" if metric["pct_exact"] is not None else "n/a"
    pct_within_str = (
        f"{metric['pct_within_one']:.3f}" if metric["pct_within_one"] is not None else "n/a"
    )
    lines.append(f"- Kappa: {kappa_str}")
    lines.append(f"- Pct exact agreement: {pct_exact_str}")
    lines.append(f"- Pct within one level: {pct_within_str}")
    lines.append("")
    return lines


def format_markdown_report(report: dict, rater_names: list) -> str:
    lines = [
        "# Expert review inter-rater agreement report",
        "",
        f"Raters ({len(rater_names)}): {', '.join(rater_names)}",
        "",
        "This corpus is synthetic: every claim, document and reference answer is "
        "generated test fixture data, not a real claim file.",
        "",
        "## Agreement metrics",
        "",
    ]
    lines += _format_metric_section(
        "Verdict (would a handler act on this answer as written?)", report["verdict"]
    )
    lines += _format_metric_section(
        "Practice-match score (1-5)", report["practice_score"]
    )

    lines += ["## Disagreements", ""]
    for title, key in (("Verdict", "verdict"), ("Practice-match score", "practice_score")):
        disagreements = report[key]["disagreements"]
        lines.append(f"### {title}")
        lines.append("")
        if not disagreements:
            lines.append("No disagreements.")
            lines.append("")
            continue
        for entry in disagreements:
            values_str = ", ".join(f"{rater}={value}" for rater, value in entry["values"].items())
            lines.append(f"- `{entry['question_id']}`: {values_str}")
        lines.append("")

    lines += [
        "This report measures inter-rater agreement on synthetic-corpus answers, not "
        "real-claim accuracy.",
        "",
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--packet",
        action="append",
        default=[],
        metavar="RATER=PATH",
        help="Repeatable. A filled-in packet copy as rater_name=path/to/packet.md",
    )
    parser.add_argument(
        "--out",
        default="eval/expert-review/agreement-report.md",
        help="Where to write the Markdown report.",
    )
    args = parser.parse_args(argv)

    if len(args.packet) < 2:
        print("agreement.py: need at least 2 --packet arguments", file=sys.stderr)
        return 1

    packets = {}
    rater_names = []
    for spec in args.packet:
        if "=" not in spec:
            print(f"agreement.py: --packet must be RATER=PATH, got {spec!r}", file=sys.stderr)
            return 1
        rater_name, _, path_str = spec.partition("=")
        packets[rater_name] = parse_packet(Path(path_str))
        rater_names.append(rater_name)

    try:
        report = agreement_report(packets)
    except ValueError as exc:
        print(f"agreement.py: {exc}", file=sys.stderr)
        return 1

    markdown = format_markdown_report(report, rater_names)
    out_path = Path(args.out)
    out_path.write_text(markdown)
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
