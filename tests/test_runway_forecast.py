"""Runway forecast: same sample data as design/runway.html, same answers as the prototype.

Run: python -m pytest tests/test_runway_forecast.py -q
"""
from datetime import date

from app.services import runway_forecast as rf

TODAY = date(2026, 10, 17)
HIST = rf.m_range("2025-12", "2026-09")


def monthly(i, cat, name, amount, start="2025-12", before=None, raise_pct=0):
    return {"id": i, "category_id": cat, "name": name, "how": "monthly", "amount": amount, "start": start, "end": None,
            "raise": raise_pct, "months": {m: (before if before and m < "2026-04" else amount) for m in HIST if m >= start}}


def varies(i, cat, name, amount, series, start="2025-12"):
    ms = [m for m in HIST if m >= start]
    return {"id": i, "category_id": cat, "name": name, "how": "varies", "amount": amount, "start": start, "end": None,
            "raise": 0, "months": dict(zip(ms, series))}


def once(i, cat, name, months):
    return {"id": i, "category_id": cat, "name": name, "how": "once", "amount": 0, "start": "2025-12", "end": None, "raise": 0, "months": months}


def sample():
    travel = varies(17, 4, "Travel", 30000, [12000, 18000, 15500, 22000, 26000, 19000, 31000, 28500, 34000], start="2026-01")
    del travel["months"]["2026-09"]   # not entered yet
    rev = {"2026-03": 52000, "2026-04": 108000, "2026-05": 176000, "2026-06": 259000, "2026-07": 348000, "2026-09": 541000}
    bank = {"2026-03": 20300, "2026-04": 41000, "2026-05": 66000, "2026-06": 96500, "2026-07": 128800, "2026-09": 199100}
    figs = [("2026-08-06", 83000, 30900, None), ("2026-08-19", 268000, 99300, 438000), ("2026-09-02", 31000, 11500, None),
            ("2026-09-11", 162000, 59800, 521000), ("2026-09-22", 389000, 143500, 536000), ("2026-10-09", 186000, 68600, None),
            ("2026-10-16", 322000, 118900, 628000)]
    return {
        "settings": {"start_month": "2025-12", "buffer_pct": 5, "green_months": 12, "amber_months": 6},
        "categories": [{"id": 1, "name": "IT"}, {"id": 2, "name": "Admin"}, {"id": 3, "name": "Compliance"}, {"id": 4, "name": "Business Development"}],
        "expenses": [
            monthly(1, 1, "Tech lead salary", 150000, before=140000, raise_pct=8),
            monthly(2, 1, "Backend developer salary", 90000, before=85000, raise_pct=8),
            monthly(3, 1, "Frontend developer salary", 80000, before=75000, raise_pct=8),
            monthly(4, 1, "QA engineer salary", 55000, start="2026-03", raise_pct=8),
            varies(5, 1, "AWS", 42000, [21000, 22500, 23800, 26100, 27400, 30200, 33100, 35800, 38900, 41600]),
            monthly(6, 1, "Software subscriptions", 18000),
            once(7, 1, "Laptops", {"2025-12": 320000, "2026-03": 72000, "2027-01": 150000}),
            monthly(8, 2, "Office rent", 60000, raise_pct=5),
            monthly(9, 2, "Internet and electricity", 9000),
            monthly(10, 2, "Admin executive salary", 35000, before=32000, raise_pct=8),
            monthly(11, 2, "Accountant", 15000),
            monthly(12, 3, "Compliance officer salary", 70000, start="2026-02", raise_pct=8),
            monthly(13, 3, "Legal retainer", 25000),
            once(14, 3, "Audit and filings", {"2026-01": 45000, "2026-05": 120000, "2026-09": 38000, "2027-05": 130000}),
            once(15, 3, "Security audit (VAPT)", {"2026-07": 180000, "2027-07": 180000}),
            monthly(16, 4, "BD manager salary", 90000, start="2026-04", raise_pct=8),
            travel,
        ],
        "products": [{
            "id": 1, "name": "GMDI", "tracking": "figures", "cost_label": "Bank charges", "amount": 0, "start": "2025-12", "end": None,
            "figures": [{"id": i, "day": d, "revenue": r, "cost": c, "forecast": f} for i, (d, r, c, f) in enumerate(figs)],
            "months": {m: {"revenue": rev[m], "cost": bank[m]} for m in rev},
        }],
        "capital": [{"day": "2025-12-01", "amount": 7000000}, {"day": "2026-06-10", "amount": 5000000}],
        "bank": {"day": "2026-09-30", "amount": 5620000},
    }


def test_sample_forecast():
    # The prototype's numbers (break-even Sep 2027) came from a trend that kept early growth going for
    # years (phi up to 0.98). Capped at 0.9 the same data gives no break-even inside three years unless
    # income comes in at the high end, which is what the page now says, with a range.
    s = rf.build(sample(), TODAY)
    assert s["state"] == "risk"
    assert s["expected"]["be"] is None
    assert s["faster"]["be"] == "2027-08"
    assert s["expected"]["out"] == "2028-10" and s["slower"]["out"] == "2027-10"
    assert s["confidence"]["be_range"] == {"early": "2027-08", "expected": None, "late": None}
    assert round(s["cash_now"] / 1e5, 1) == 57.5
    assert round(s["now"]["cost"] / 1e5, 1) == 10.4
    assert round(s["now"]["inc"] / 1e5, 1) == 6.2


def test_current_month_pace_from_irregular_figures():
    s = rf.build(sample(), TODAY)
    cur = s["products"][0]["current"]
    assert cur["day"] == 16 and cur["so_far"] == 322000
    assert round(cur["value"]) == 622804          # (pace projection + GMDI's own 6.28 L) / 2
    assert cur["lo"] < cur["value"] < cur["hi"]


def test_skipped_month_end_is_estimated_and_flagged():
    s = rf.build(sample(), TODAY)
    aug = next(r for r in s["products"][0]["months"] if r["month"] == "2026-08")
    assert aug["how"] == "estimated"
    kinds = {t["kind"] for t in s["todo"]}
    assert "product_month_estimated" in kinds
    missing = next(t for t in s["todo"] if t["kind"] == "expenses_missing")
    assert missing["names"] == ["Travel"]


def test_second_product_adds_income_and_moves_break_even_earlier():
    base = rf.build(sample(), TODAY)
    d = sample()
    d["products"].append({"id": 2, "name": "Build-Track", "tracking": "fixed", "cost_label": None, "amount": 150000,
                          "start": "2026-11", "end": None, "figures": [], "months": {}})
    s = rf.build(d, TODAY)
    nov = next(m for m in s["months"] if m["month"] == "2026-11")
    nov0 = next(m for m in base["months"] if m["month"] == "2026-11")
    assert round(nov["inc"] - nov0["inc"]) == 150000
    end = s["months"][-1]["month"]
    assert s["grid"]["cash"][end] > base["grid"]["cash"][end]
    assert rf.m_idx(s["faster"]["be"]) <= rf.m_idx(base["faster"]["be"])


def test_money_running_out_before_break_even_is_risk():
    d = sample()
    d["capital"] = d["capital"][:1]
    s = rf.build(d, TODAY)
    assert s["state"] == "risk"
    assert s["expected"]["out"] is not None and s["need"] > 0


def test_no_data_yet_does_not_crash():
    d = {"settings": {"start_month": "2025-12", "buffer_pct": 5, "green_months": 12, "amber_months": 6},
         "categories": [], "expenses": [], "products": [{"id": 1, "name": "GMDI", "tracking": "figures", "cost_label": "Bank charges",
         "amount": 0, "start": "2025-12", "end": None, "figures": [], "months": {}}], "capital": [], "bank": None}
    s = rf.build(d, TODAY)
    assert s["now"]["cost"] == 0 and s["state"] == "self_funding"


def test_same_every_month_counts_past_months_without_typing_each_one():
    d = sample()
    d["expenses"].append({"id": 99, "category_id": 2, "name": "New office rent", "how": "monthly", "amount": 50000,
                          "start": "2026-01", "end": None, "raise": 0, "months": {"2026-03": 55000}})
    base = rf.build(sample(), TODAY)
    s = rf.build(d, TODAY)
    diff = {m["month"]: round(m["cost"] - b["cost"]) for m, b in zip(s["months"], base["months"])}
    assert diff["2025-12"] == 0                 # before it started
    assert diff["2026-01"] == 50000 and diff["2026-09"] == 50000
    assert diff["2026-03"] == 55000             # a month entered differently keeps its own figure
    assert all("New office rent" not in t.get("names", []) for t in s["todo"])   # never "not entered"


# ---------------------------------------------------------------- billed monthly with minimums (GMDI)

def billed_sample(delay=1):
    d = sample()
    g = d["products"][0]
    g.update({"tracking": "billed", "pay_delay": delay, "clients": [
        {"id": 1, "name": "Acme", "minimum": 20000, "fixed": 0, "start": "2026-03", "end": None},
        {"id": 2, "name": "Brightline", "minimum": 50000, "fixed": 5000, "start": "2026-05", "end": None},
        {"id": 3, "name": "Cobalt (signed)", "minimum": 100000, "fixed": 0, "start": "2027-02", "end": None},
    ]})
    return d


def test_forecast_never_below_the_clients_minimums():
    d = billed_sample(delay=0)
    d["products"][0]["months"] = {"2026-07": {"revenue": 80000, "cost": 10000}, "2026-08": {"revenue": 76000, "cost": 9000},
                                  "2026-09": {"revenue": 75000, "cost": 9000}}
    d["products"][0]["figures"] = []
    s = rf.build(d, TODAY)
    p = s["products"][0]
    assert p["committed"] == 20000 + 55000
    inc = {m["month"]: m["inc"] for m in s["months"]}
    assert all(inc[m] >= 75000 - 1 for m in rf.m_range("2026-10", "2027-01"))
    assert all(inc[m] >= 175000 - 1 for m in rf.m_range("2027-02", "2027-06"))   # Cobalt's minimum from Feb


def test_gmdi_expected_bill_is_taken_as_is():
    d = billed_sample(delay=0)
    d["products"][0]["figures"].append({"id": 99, "day": "2026-10-16", "revenue": 9000, "cost": 4000, "forecast": 210000})
    s = rf.build(d, TODAY)
    cur = s["products"][0]["current"]
    assert cur["source"] == "product" and cur["value"] == 210000 and cur["expected_cost"] == 4000
    assert round(s["now"]["inc"]) == 210000


def test_usage_below_minimum_is_billed_the_minimum():
    d = billed_sample(delay=0)
    d["products"][0]["figures"] = [{"id": 1, "day": "2026-10-10", "revenue": 3000, "cost": 500, "forecast": None},
                                   {"id": 2, "day": "2026-10-16", "revenue": 5000, "cost": 900, "forecast": None}]
    s = rf.build(d, TODAY)
    cur = s["products"][0]["current"]
    assert cur["source"] == "usage" and cur["value"] == 75000   # ~₹9.7k usage, but clients' minimums are ₹75k


def test_paid_the_month_after():
    now0 = rf.build(billed_sample(delay=0), TODAY)
    now1 = rf.build(billed_sample(delay=1), TODAY)
    sep_bill = 541000
    assert round(now1["now"]["inc"]) == sep_bill                      # October's money is September's bill
    m0 = {m["month"]: m for m in now0["months"]}; m1 = {m["month"]: m for m in now1["months"]}
    assert round(m1["2026-08"]["inc"]) == round(m0["2026-07"]["inc"])
    assert round(m1["2026-07"]["cost"]) == round(m0["2026-07"]["cost"])   # bank charges stay in their own month
    assert now1["cash_now"] < now0["cash_now"]                       # September's bill is not in the bank yet


# ----------------------------------------------------------------- 4 Nov: forecast fixes + confidence

def bare(products=None, expenses=None, capital=None):
    return {"settings": {"start_month": "2026-07", "buffer_pct": 5, "green_months": 12, "amber_months": 6},
            "categories": [{"id": 1, "name": "Team"}], "expenses": expenses or [], "products": products or [],
            "capital": capital or [{"day": "2026-07-01", "amount": 5000000}], "bank": None}


def gmdi(months, tracking="figures", clients=None):
    return {"id": 1, "name": "GMDI", "tracking": tracking, "cost_label": None, "amount": 0, "start": "2026-07", "end": None,
            "pay_delay": 0, "clients": clients or [], "figures": [], "months": {m: {"revenue": v, "cost": 0} for m, v in months.items()}}


NOV = date(2026, 11, 4)


def test_short_history_is_not_stretched_into_growth():
    # Three months of fast early growth: the old method projected that growth for years.
    d = bare([gmdi({"2026-08": 100000, "2026-09": 200000, "2026-10": 300000})])
    s = rf.build(d, NOV)
    g = s["products"][0]["grid"]
    assert s["products"][0]["trend"] is False
    assert g["2027-06"]["v"] == g["2026-12"]["v"]          # a level, not a growth curve
    assert g["2026-12"]["v"] < 300000 * 1.01               # never above the latest real month
    assert s["confidence"]["label"] == "low"
    assert any(x["key"] == "history" for x in s["confidence"]["gaps"])


def test_missing_month_is_not_counted_as_zero():
    full = rf.build(bare([gmdi({"2026-07": 300000, "2026-08": 300000, "2026-09": 300000, "2026-10": 300000})]), NOV)
    hole = rf.build(bare([gmdi({"2026-07": 300000, "2026-08": 300000, "2026-10": 300000})]), NOV)
    assert round(hole["products"][0]["grid"]["2027-03"]["v"]) == round(full["products"][0]["grid"]["2027-03"]["v"]) == 300000


def test_changing_expense_typed_once_repeats_like_excel():
    e = {"id": 1, "category_id": 1, "name": "Team salaries", "how": "varies", "amount": 0, "start": "2026-07", "end": None,
         "raise": 0, "months": {"2026-10": 800000}}
    s = rf.build(bare(expenses=[e]), NOV)
    assert s["grid"]["expenses"][1]["2027-02"] == {"v": 800000, "how": "rule"}
    assert s["grid"]["expenses"][1]["2026-10"]["how"] == "typed"


def test_one_off_income_and_typed_future_months():
    fees = {"id": 2, "name": "Onboarding fees", "tracking": "once", "cost_label": None, "amount": 0, "start": "2026-07", "end": None,
            "pay_delay": 0, "clients": [], "figures": [], "months": {"2026-09": {"revenue": 50000, "cost": 0}, "2027-01": {"revenue": 75000, "cost": 0}}}
    p = gmdi({"2026-08": 100000, "2026-09": 100000, "2026-10": 100000})
    p["months"]["2027-03"] = {"revenue": 400000, "cost": 0}      # a plan typed into the future
    s = rf.build(bare([p, fees]), NOV)
    f = s["products"][1]["grid"]
    assert set(f) == {"2026-09", "2027-01"} and f["2027-01"] == {"v": 75000, "cost": 0, "how": "typed"}
    g = s["products"][0]["grid"]
    assert g["2027-03"]["v"] == 400000 and g["2027-03"]["how"] == "typed"
    assert g["2027-04"]["how"] == "forecast"
    assert s["grid"]["income_total"]["2027-01"] == g["2027-01"]["v"] + 75000


def test_confidence_rises_with_real_data():
    thin = rf.build(bare([gmdi({"2026-10": 300000})]), NOV)["confidence"]
    months = {rf.m_of(rf.m_idx("2026-01") + i): 100000 + i * 20000 for i in range(10)}
    rich_d = bare([gmdi(months, tracking="billed", clients=[{"name": "A", "minimum": 200000, "fixed": 0, "start": "2026-01", "end": None}])])
    rich_d["settings"]["start_month"] = "2026-01"
    rich_d["bank"] = {"day": "2026-10-31", "amount": 4000000}
    rich_d["products"][0]["figures"] = [{"id": 1, "day": "2026-11-03", "revenue": 20000, "cost": None, "forecast": None}]
    rich_d["expenses"] = [{"id": 1, "category_id": 1, "name": "Salaries", "how": "monthly", "amount": 500000, "start": "2026-01", "end": None, "raise": 0, "months": {}}]
    rich = rf.build(rich_d, NOV)["confidence"]
    assert thin["score"] < 45 <= rich["score"]
    assert {"history", "bank"} <= {g["key"] for g in thin["gaps"]}


# ---------------------------------------------------------------- received vs expected

def test_paid_less_is_carried_as_owed_into_this_month():
    base = rf.build(billed_sample(delay=1), TODAY)
    p0 = base["products"][0]
    sep_expected = p0["cash"]["2026-09"]["expected"]          # August's bill, due in September
    d = billed_sample(delay=1)
    d["products"][0]["received"] = {"2026-09": sep_expected - 100000}
    s = rf.build(d, TODAY)
    p = s["products"][0]
    assert round(p["cash"]["2026-09"]["cash"]) == round(sep_expected - 100000)
    assert round(p["owed_now"]) == 100000
    assert round(p["paid_this_month"] - p0["paid_this_month"]) == 100000   # the arrears are expected now
    assert round(s["cash_now"] - base["cash_now"]) == -100000               # and are not in the bank yet


def test_paid_ahead_reduces_what_is_expected_next():
    base = rf.build(billed_sample(delay=1), TODAY)
    p0 = base["products"][0]
    sep_expected = p0["cash"]["2026-09"]["expected"]
    d = billed_sample(delay=1)
    d["products"][0]["received"] = {"2026-09": sep_expected + 50000}        # paid part of October's in September
    p = rf.build(d, TODAY)["products"][0]
    assert round(p["owed_now"]) == -50000
    assert round(p0["paid_this_month"] - p["paid_this_month"]) == 50000


def test_no_receipts_changes_nothing():
    a = rf.build(billed_sample(delay=1), TODAY)
    d = billed_sample(delay=1); d["products"][0]["received"] = {}
    b = rf.build(d, TODAY)
    assert a["expected"] == b["expected"] and a["cash_now"] == b["cash_now"]
