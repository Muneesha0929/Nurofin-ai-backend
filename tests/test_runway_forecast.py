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


def test_matches_the_prototype():
    s = rf.build(sample(), TODAY)
    assert s["state"] == "good"
    assert s["expected"]["be"] == "2027-09"
    assert s["expected"]["back"] == "2029-08"
    assert s["expected"]["low"] == "2027-08"
    assert round(s["expected"]["low_cash"] / 1e5, 1) == 29.8
    assert round(s["cash_now"] / 1e5, 1) == 57.5
    assert round(s["now"]["cost"] / 1e5, 1) == 10.4
    assert round(s["now"]["inc"] / 1e5, 1) == 6.2
    assert s["slower"]["out"] == "2029-03"


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
    assert rf.m_idx(s["expected"]["be"]) < rf.m_idx(base["expected"]["be"])


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
