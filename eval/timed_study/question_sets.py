"""Matched 10/10 question sets for the handler timed study (plan.md, "b. Timed study").

Selects 20 of the 34 entries in eval/golden_queries.py's GOLDEN_QUERIES and splits
them into SET_A and SET_B, matched pairwise on a difficulty proxy so the two sets
have the same difficulty distribution, not just the same size.

Difficulty proxy: `(scope, num_sources)` where
  - `scope` is `"claim"` if the query's `claim_id` is set, else `"global"` --
    claim-scoped questions need the handler to locate a specific claim's dossier
    in addition to policy/guideline material, so they are coarsely harder.
  - `num_sources` is the number of source documents the question's own `source`
    field names: 0 for `None` (the hallucination probe, which needs the handler
    to establish nothing supports an answer), 1 for a single filename, or the
    list length when `source` is a list of filenames.
This reuses a field every golden query already carries (`source`) rather than
inventing a new one, per the task brief.

Selection is a fixed deterministic rule: within each `(scope, num_sources)`
category, GOLDEN_QUERIES' ids are shuffled with `random.Random(42)`, then the
category's quota (below) is taken from the front of that shuffled order and
split evenly, alternating, between SET_A and SET_B. Categories with fewer than
2 members (so they can't be split evenly between the two sets) are skipped
entirely -- in the current 34-question corpus that's the single hallucination
probe (`scope="claim", num_sources=0`) and the single 3-source SIU checklist
(`scope="claim", num_sources=3`).

Quotas (10 per set, 20 total): 6+6 from `("global", 1)`, 3+3 from
`("claim", 1)`, 1+1 from `("claim", 2)`.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from golden_queries import GOLDEN_QUERIES  # noqa: E402

_SEED = 42

# (scope, num_sources) -> number of ids to take from that category, pre-split.
_CATEGORY_QUOTAS = {
    ("global", 1): 12,
    ("claim", 1): 6,
    ("claim", 2): 2,
}

_BY_ID = {q["id"]: q for q in GOLDEN_QUERIES}


def difficulty_proxy(query: dict) -> tuple[str, int]:
    scope = "global" if query.get("claim_id") is None else "claim"
    source = query.get("source")
    if source is None:
        num_sources = 0
    elif isinstance(source, list):
        num_sources = len(source)
    else:
        num_sources = 1
    return (scope, num_sources)


def _categorize() -> dict[tuple[str, int], list[str]]:
    buckets: dict[tuple[str, int], list[str]] = {}
    for query in GOLDEN_QUERIES:
        buckets.setdefault(difficulty_proxy(query), []).append(query["id"])
    return buckets


def _build_sets() -> tuple[list[str], list[str]]:
    buckets = _categorize()
    set_a_ids: list[str] = []
    set_b_ids: list[str] = []
    for category, quota in _CATEGORY_QUOTAS.items():
        ids = sorted(buckets.get(category, []))
        random.Random(_SEED).shuffle(ids)
        chosen = ids[:quota]
        set_a_ids.extend(chosen[0::2])
        set_b_ids.extend(chosen[1::2])
    return set_a_ids, set_b_ids


_SET_A_IDS, _SET_B_IDS = _build_sets()

SET_A: list[dict] = [_BY_ID[qid] for qid in _SET_A_IDS]
SET_B: list[dict] = [_BY_ID[qid] for qid in _SET_B_IDS]


def difficulty_balance_report() -> dict:
    """Per-set counts of each (scope, num_sources) category, for test assertions."""
    report: dict[str, dict] = {"A": {}, "B": {}}
    for label, questions in (("A", SET_A), ("B", SET_B)):
        for query in questions:
            category = difficulty_proxy(query)
            key = f"{category[0]}:{category[1]}"
            report[label][key] = report[label].get(key, 0) + 1
    return report
