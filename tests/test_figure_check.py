"""Unit tests for the deterministic figure check (backend/figure_check.py)."""

from decimal import Decimal

from backend.figure_check import FigureCheck, check_figures, extract_figures, notice


def _values(text, kind):
    return [f.value for f in extract_figures(text) if f.kind == kind]


def test_money_normalizes_to_cents_and_keeps_the_sign():
    text = "Paid $1,234.5, billed $1234.50, credit -$1,234.50, cap $ 12.5 and $1234."
    assert _values(text, "money") == ["1234.50", "1234.50", "-1234.50", "12.50", "1234.00"]
    assert extract_figures("credit -$1,234.50")[0].text == "-$1,234.50"


def test_percent_forms_normalize():
    assert _values("12% then 12.5 % then 12.50 percent then 10%", "percent") == ["12", "12.5", "12.5", "10"]


def test_date_forms_give_iso_and_impossible_dates_are_ignored():
    text = "On 2026-03-14, 3/14/2026, 03/14/2026, March 14, 2026 and Mar 14, 2026; not 2026-02-30 or 2/30/2026."
    assert _values(text, "date") == ["2026-03-14"] * 5


def test_figures_come_back_in_order_of_appearance():
    kinds = [f.kind for f in extract_figures("On 2026-01-14 a 20% share of $500 was paid.")]
    assert kinds == ["date", "percent", "money"]


def test_figure_in_sources_only_counts_as_source_only_not_verified():
    check = check_figures("Deductible $500 on 2026-01-14.", trusted="Total $4,850.00",
                          sources=["The $500.00 deductible", "Received 1/14/2026"])
    assert check.checked == 2
    assert check.unverified == []
    assert [(f.kind, f.text) for f in check.source_only] == [("money", "$500"), ("date", "2026-01-14")]
    assert check.to_dict()["action"] == "source_only"


def test_figure_in_trusted_counts_as_verified_even_if_also_in_sources():
    check = check_figures("Deductible $500.", trusted="Total $500.00",
                          sources=["The $500.00 deductible"])
    assert check.unverified == []
    assert check.source_only == []
    assert check.to_dict()["action"] == "none"


def test_unverified_figures_are_deduped_in_first_seen_order():
    check = check_figures("Pay $999.99, 12%, then $999.990 and $999.99 again; $75 is fine.",
                          trusted="| Body | $75.00/hr |", sources=[])
    assert check.checked == 3
    assert [(f.kind, f.text) for f in check.unverified] == [("money", "$999.99"), ("percent", "12%")]
    assert check.to_dict()["action"] == "flagged"
    assert check.to_dict()["unverified"] == [{"kind": "money", "text": "$999.99"}, {"kind": "percent", "text": "12%"}]


def test_to_dict_is_unchecked_when_the_answer_has_no_figures():
    check = check_figures("No figures here.", trusted="", sources=[])
    assert check.checked == 0
    assert check.to_dict()["action"] == "unchecked"


def test_notice_names_figures_and_caps_the_list_at_eight():
    one = check_figures("It costs $999.99 at 12%.", trusted="", sources=[])
    assert notice(one) == ("> **Figure check:** 2 figure(s) in this answer do not match the computed figures "
                           "or the cited sources: `$999.99`, `12%`. Rely on the computed figures below.")
    many = check_figures(" ".join(f"${n}" for n in range(1, 12)), trusted="", sources=[])
    text = notice(many)
    assert text.startswith("> **Figure check:** 11 figure(s)")
    assert "`$8`, and 3 more." in text
    assert "`$9`" not in text


def test_notice_adds_a_second_line_for_source_only_figures():
    check = check_figures("Shop invoice shows $95/hr.", trusted="", sources=["Rate: $95/hr"])
    text = notice(check)
    assert "come only from the claim documents" in text
    assert "`$95`" in text
    assert "Verify them against the source." in text


def test_notice_is_empty_when_nothing_is_flagged():
    assert notice(FigureCheck(checked=3)) == ""
    assert notice(check_figures("No figures here.", trusted="", sources=[])) == ""


def test_money_sign_variants_all_normalize_the_same():
    assert _values("-$865.00", "money") == ["-865.00"]
    assert _values("$-865.00", "money") == ["-865.00"]
    assert _values("($865.00)", "money") == ["-865.00"]
    assert _values("$865.00", "money") == ["865.00"]


def test_money_magnitude_suffixes_scale_the_value():
    text = "$865k, $1.2m, $3 million, $40 thousand, $2bn"
    assert _values(text, "money") == ["865000.00", "1200000.00", "3000000.00", "40000.00", "2000000000.00"]


def test_negative_figure_from_claims_tools_fmt_is_verified_in_any_sign_form():
    from backend.claims_tools import fmt

    trusted_text = f"Net recovery: {fmt(Decimal('-865.00'))}"  # claims_tools renders "-$865.00"
    for restated in ("The net recovery is -$865.00.", "The net recovery is $-865.00.",
                     "The net recovery is ($865.00)."):
        check = check_figures(restated, trusted=trusted_text, sources=[])
        assert check.unverified == [], restated


def test_money_without_dollar_sign():
    assert _values("865 dollars", "money") == ["865.00"]
    assert _values("USD 500", "money") == ["500.00"]
    assert _values("500 USD", "money") == ["500.00"]


def test_malformed_thousands_grouping_is_not_truncated():
    figures = extract_figures("Pay $1,2345 today.")
    assert len(figures) == 1
    assert figures[0].text == "$1,2345"
    assert figures[0].value != "1234.00"
    assert figures[0].value.startswith("malformed:")
    # The same malformed text elsewhere still "matches" (it's an opaque identical value),
    # but it never collides with a real, correctly-grouped amount.
    check = check_figures("Pay $1,2345 today.", trusted="", sources=["Invoice: $1,2345"])
    assert check.unverified == []
    assert len(check.source_only) == 1


def test_very_large_amount_is_unverified_not_dropped():
    huge = "$" + "9" * 400 + ".00"
    figures = extract_figures(huge)
    assert len(figures) == 1
    check = check_figures(huge, trusted="", sources=[])
    assert check.checked == 1
    assert len(check.unverified) == 1


def test_two_digit_years_and_day_first_named_dates():
    assert _values("10/22/26", "date") == ["2026-10-22"]
    assert _values("10/22/99", "date") == ["1999-10-22"]
    assert _values("22 October 2026", "date") == ["2026-10-22"]
    assert _values("22 Oct 2026", "date") == ["2026-10-22"]


def test_numeric_date_day_first_when_first_field_exceeds_twelve():
    assert _values("22/10/2026", "date") == ["2026-10-22"]
    assert _values("13/02/2026", "date") == ["2026-02-13"]


def test_pathological_input_stays_linear_time():
    import time

    text = ("$1,234.56 " * 30000) + "x" * 10000
    start = time.monotonic()
    extract_figures(text)
    assert time.monotonic() - start < 1.0


def test_ordinary_prose_after_an_amount_does_not_scale_or_break_it():
    # Regressions caught in review of the suffix/grouping/sign rules: none of these may flag a correct figure.
    assert _values("It costs $865 more than that.", "money") == ["865.00"]
    assert _values("Paid $500 by check.", "money") == ["500.00"]
    assert _values("The deductible is $1,000, and labor is $95.", "money") == ["1000.00", "95.00"]
    assert _values("Total: $1,234.56.", "money") == ["1234.56"]


def test_a_dash_between_two_amounts_is_a_range_not_a_sign():
    assert _values("Range $500-$800.", "money") == ["500.00", "800.00"]
    assert _values("x 10% - $5", "money") == ["5.00"]
    assert _values("net -$865.00", "money") == ["-865.00"]
