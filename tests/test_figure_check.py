"""Unit tests for the deterministic figure check (backend/figure_check.py)."""

from backend.figure_check import FigureCheck, check_figures, extract_figures, notice


def _values(text, kind):
    return [f.value for f in extract_figures(text) if f.kind == kind]


def test_money_normalizes_to_cents_and_drops_the_sign():
    text = "Paid $1,234.5, billed $1234.50, credit -$1,234.50, cap $ 12.5 and $1234."
    assert _values(text, "money") == ["1234.50", "1234.50", "1234.50", "12.50", "1234.00"]
    assert extract_figures("credit -$1,234.50")[0].text == "-$1,234.50"


def test_percent_forms_normalize():
    assert _values("12% then 12.5 % then 12.50 percent then 10%", "percent") == ["12", "12.5", "12.5", "10"]


def test_date_forms_give_iso_and_impossible_dates_are_ignored():
    text = "On 2026-03-14, 3/14/2026, 03/14/2026, March 14, 2026 and Mar 14, 2026; not 2026-02-30 or 2/30/2026."
    assert _values(text, "date") == ["2026-03-14"] * 5


def test_figures_come_back_in_order_of_appearance():
    kinds = [f.kind for f in extract_figures("On 2026-01-14 a 20% share of $500 was paid.")]
    assert kinds == ["date", "percent", "money"]


def test_figure_in_evidence_only_counts_as_verified():
    check = check_figures("Deductible $500 on 2026-01-14.", trusted="Total $4,850.00",
                          evidence=["The $500.00 deductible", "Received 1/14/2026"])
    assert check.checked == 2
    assert check.unverified == []
    assert check.to_dict() == {"action": "none", "checked": 2, "unverified": []}


def test_unverified_figures_are_deduped_in_first_seen_order():
    check = check_figures("Pay $999.99, 12%, then $999.990 and $999.99 again; $75 is fine.",
                          trusted="| Body | $75.00/hr |", evidence=[])
    assert check.checked == 3
    assert [(f.kind, f.text) for f in check.unverified] == [("money", "$999.99"), ("percent", "12%")]
    assert check.to_dict()["action"] == "flagged"
    assert check.to_dict()["unverified"] == [{"kind": "money", "text": "$999.99"}, {"kind": "percent", "text": "12%"}]


def test_notice_names_figures_and_caps_the_list_at_eight():
    one = check_figures("It costs $999.99 at 12%.", trusted="", evidence=[])
    assert notice(one) == ("> **Figure check:** 2 figure(s) in this answer do not match the computed figures "
                           "or the cited sources: `$999.99`, `12%`. Rely on the computed figures below.")
    many = check_figures(" ".join(f"${n}" for n in range(1, 12)), trusted="", evidence=[])
    text = notice(many)
    assert text.startswith("> **Figure check:** 11 figure(s)")
    assert "`$8`, and 3 more." in text
    assert "`$9`" not in text


def test_notice_is_empty_when_nothing_is_flagged():
    assert notice(FigureCheck(checked=3)) == ""
    assert notice(check_figures("No figures here.", trusted="", evidence=[])) == ""
