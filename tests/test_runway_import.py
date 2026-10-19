"""Runway bulk upload: the parser copes with real Excel exports and explains every problem."""
from datetime import date

from app.services import runway_import as ri
from tests.test_runway_forecast import sample

TODAY = date(2026, 10, 17)


def empty():
    d = sample()
    d["expenses"], d["capital"] = [], []
    d["products"][0]["months"], d["products"][0]["figures"] = {}, []
    return d


def test_month_headings_excel_style():
    for h in ("2025-12", "Dec 2025", "December 2025", "Dec-25", "dec'25", "12/2025", "01-12-2025", "2025-12-01", "1-Dec-25"):
        assert ri.parse_month(h) == "2025-12", h
    assert ri.parse_month("Name") is None and ri.parse_month("13/2025") is None


def test_dates_day_first():
    for d in ("2026-10-16", "16/10/2026", "16-10-26", "16 Oct 2026", "16-Oct-26"):
        assert ri.parse_day(d) == "2026-10-16", d
    assert ri.parse_day("31/02/2026") is None


def test_amounts_indian_style():
    assert ri.parse_amount("₹1,50,000") == (150000, None)
    assert ri.parse_amount("Rs. 42,000") == (42000, None)
    assert ri.parse_amount("1.5L") == (150000, None)
    assert ri.parse_amount("2 Cr") == (2e7, None)
    assert ri.parse_amount("12k") == (12000, None)
    assert ri.parse_amount("") == (None, None) and ri.parse_amount("-") == (None, None)
    assert ri.parse_amount("abc")[1] and ri.parse_amount("(500)")[1]


def test_backfill_from_scratch():
    csv = "﻿Type;Name;Category;How often;Yearly raise %;Dec-25;Jan-26;Feb-26;Mar-26\n" \
          "Expense;Tech lead salary;IT;Every month;8;1,40,000;1,40,000;1,40,000;1,40,000\n" \
          "Expense;AWS;IT;;;21000;22500;23800;26100\n" \
          "Expense;Laptops;IT;;;3.2L;;;72000\n" \
          "Expense;Old intern;Interns;;;15000;15000;;\n" \
          "Income;GMDI;;;;;;;52000\n" \
          "Income cost;GMDI;Bank charges;;;;;;20300\n" \
          "Capital;Initial capital;;;;70L;;;\n"
    plan = ri.parse(csv, empty(), TODAY)
    assert plan["errors"] == [], plan["errors"]
    assert plan["categories"] == ["Interns"]             # IT already exists, Interns is new
    exp = {e["name"]: e for e in plan["expenses"]}
    assert exp["Tech lead salary"]["how"] == "monthly" and exp["Tech lead salary"]["raise_pct"] == 8
    assert exp["AWS"]["how"] == "varies"                 # inferred: amounts change
    assert exp["Laptops"]["how"] == "once"               # inferred: two separate payments
    assert exp["Old intern"]["end_month"] == "2026-01"   # stopped after January
    s = plan["summary"]
    assert s["new_values"] == 4 + 4 + 2 + 2 + 1 + 1 and s["changed_values"] == 0
    gm = next(c for c in plan["cells"] if c["target"] == "product_month")
    assert gm["amount"] == 52000 and gm["cost"] == 20300


def test_template_round_trip_changes_nothing():
    d = sample()
    d["capital"] = [{"id": 1, "day": "2025-12-01", "amount": 7000000, "note": "Initial capital"}]
    plan = ri.parse(ri.template_monthly(d, TODAY), d, TODAY)
    assert plan["errors"] == [], plan["errors"]
    assert plan["summary"]["new_values"] == 0 and plan["summary"]["changed_values"] == 0
    assert plan["expenses"] == [] and plan["categories"] == []
    figs = ri.parse(ri.template_figures(d), d, TODAY)
    assert figs["errors"] == [] and figs["summary"]["new_values"] == 0 and figs["summary"]["changed_values"] == 0


def test_changed_cell_is_reported():
    d = sample()
    text = ri.template_monthly(d, TODAY).replace("Tech lead salary,IT,Every month,8,140000", "Tech lead salary,IT,Every month,8,145000")
    plan = ri.parse(text, d, TODAY)
    assert plan["summary"]["changed_values"] == 1
    ch = plan["summary"]["changes"][0]
    assert ch["name"] == "Tech lead salary" and ch["month"] == "2025-12" and ch["old"] == 140000 and ch["new"] == 145000


def test_problems_have_row_and_column():
    csv = "Type,Name,Category,How often,Yearly raise %,Dec 2025,Nov 2025,Oct 2026\n" \
          "Expense,New thing,,,,100,,\n" \
          "Expense,Bad amount,IT,,,abc,,\n" \
          "Expenses,Too early,IT,,,,500,\n" \
          "Income,GMDI,,,,,,500\n" \
          "Spending,Odd,IT,,,1,,\n" \
          "Expense,New thing,IT,,,1,,\n"
    errs = ri.parse(csv, empty(), TODAY)["errors"]
    msgs = " | ".join(f"{e['row']}:{e['column']}:{e['message']}" for e in errs)
    assert "2:Category" in msgs                      # new expense needs a category
    assert "3:Dec 2025" in msgs and "abc" in msgs    # not an amount
    assert "4:Nov 2025" in msgs and "before the books start" in msgs
    assert "5:Oct 2026" in msgs and "months that are over" in msgs
    assert "6:Type" in msgs
    assert "7:Name" in msgs                          # same line twice


def test_figures_file_irregular_dates():
    csv = "Product,Date,Revenue so far this month,Cost so far this month\n" \
          "GMDI,06/08/2026,83000,30900\nGMDI,19/08/2026,2.68L,99300\nGMDI,02/09/2026,31000,\nGMDI,16/10/2026,322000,118900\n"
    plan = ri.parse(csv, empty(), TODAY)
    assert plan["kind"] == "figures" and plan["errors"] == []
    assert [c["day"] for c in plan["cells"]] == ["2026-08-06", "2026-08-19", "2026-09-02", "2026-10-16"]
    bad = ri.parse(csv + "GMDI,17/10/2026,100000,\nGMDI,30/10/2026,1,\n", empty(), TODAY)["errors"]
    assert any("lower than" in e["message"] for e in bad) and any("future" in e["message"] for e in bad)
