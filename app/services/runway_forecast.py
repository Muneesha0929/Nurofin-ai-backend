"""The Runway forecast: plain functions over plain data, so it can be tested without a database.

Method, in short (the page's "How these numbers are worked out" says the same in plain words):

- Going out: expenses as entered. 'monthly' lines keep their amount and rise by raise_pct each
  April; 'varies' lines follow their own trend; 'once' lines only count in months they have an
  entry. A safety buffer (default 5%) is added to every month from the current one on.
- Coming in: each product separately.
  * 'fixed' products: their amount every month between start and end.
  * 'figures' products: a damped Holt trend (level + trend, the trend fading each month) fitted
    to the product's month totals. The smoothing settings are picked by trying every
    combination on the past months and keeping the one that would have predicted best.
    The current month comes from the "so far" figures: money so far / days, the latest stretch
    weighted up to 60% (less when it covers under a week), averaged with the product's own
    month-end forecast when one was typed in.
  * A product's linked cost (bank charges for GMDI) is counted as going out, at the same share
    of revenue as its last three months.
- Two cases: expected (middle of the forecast) and slower (low end of the 80% range for income,
  costs +5% more). "On track" is judged on the slower case on purpose.
"""
from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any, Dict, List, Optional

Z80 = 1.2816
HORIZON = 36  # months shown ahead, current month included


# ----------------------------------------------------------------- months

def m_idx(m: str) -> int:
    y, mo = m.split("-")
    return int(y) * 12 + int(mo) - 1


def m_of(i: int) -> str:
    return f"{i // 12}-{i % 12 + 1:02d}"


def add_m(m: str, n: int) -> str:
    return m_of(m_idx(m) + n)


def m_range(a: str, b: str) -> List[str]:
    return [m_of(i) for i in range(m_idx(a), m_idx(b) + 1)]


def days_in(m: str) -> int:
    return calendar.monthrange(int(m[:4]), int(m[5:]))[1]


def days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


# ----------------------------------------------------------------- damped Holt

def _holt_run(y: List[float], a: float, b: float, phi: float):
    level, trend = y[0], y[1] - y[0]
    res = []
    for t in range(1, len(y)):
        pred = level + phi * trend
        res.append(y[t] - pred)
        prev = level
        level = a * y[t] + (1 - a) * (level + phi * trend)
        trend = b * (level - prev) + (1 - b) * phi * trend
    return level, trend, res


class Fit:
    def __init__(self, y: List[float]):
        self.flat = len(y) < 3
        if self.flat:
            self.mean = sum(y) / len(y) if y else 0.0
            self.spread = (max(y) - min(y)) / 2 if len(y) > 1 else self.mean * 0.25
            self.params = None
            return
        best = None
        for a in (.2, .3, .4, .5, .6, .7, .8, .9):
            for b in (.05, .1, .2, .3, .4):
                for phi in (.85, .9, .95, .98):
                    level, trend, res = _holt_run(y, a, b, phi)
                    sse = sum(e * e for e in res[1:])  # the first step only checks the starting guess
                    if best is None or sse < best[0]:
                        best = (sse, a, b, phi, level, trend, res)
        _, self.a, self.b, self.phi, self.level, self.trend, res = best
        errs = res[1:]
        self.sigma = math.sqrt(sum(e * e for e in errs) / max(len(errs), 1))
        self.params = {"alpha": self.a, "beta": self.b, "phi": self.phi}

    def at(self, h: int) -> Dict[str, float]:
        if self.flat:
            return {"v": self.mean, "lo": max(self.mean - self.spread, 0.0), "hi": self.mean + self.spread}
        damp = sum(self.phi ** k for k in range(1, h + 1))
        v = max(self.level + damp * self.trend, 0.0)
        vm = 1 + sum((self.a * (1 + j * self.b)) ** 2 for j in range(1, h))
        w = Z80 * self.sigma * math.sqrt(vm)
        return {"v": v, "lo": max(v - w, 0.0), "hi": v + w}


# ----------------------------------------------------------------- products

def pace(month: str, figs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Where a month lands, from "so far" figures taken any number of days apart."""
    last = figs[-1]
    prev = figs[-2] if len(figs) > 1 else None
    day = int(last["day"][8:])
    left = max(days_in(month) - day, 0)
    overall = last["revenue"] / max(day, 1)
    gap = max(days_between(prev["day"], last["day"]), 1) if prev else 0
    recent = (last["revenue"] - prev["revenue"]) / gap if prev else None
    w = .6 * min(1.0, gap / 7) if recent is not None else 0.0   # a stretch of a day or two is mostly noise
    rate = w * recent + (1 - w) * overall if recent is not None else overall
    run = last["revenue"] + rate * left
    vals = [run] + ([last["forecast"]] if last.get("forecast") else [])
    value = sum(vals) / len(vals)
    both = len(vals) > 1
    ratio = (last["cost"] / last["revenue"]) if last.get("cost") is not None and last["revenue"] else None
    return {
        "value": value, "lo": min(vals) * (1 if both else .92), "hi": max(vals) * (1 if both else 1.08),
        "run": run, "rate": rate, "day": day, "days": days_in(month), "so_far": last["revenue"],
        "as_of": last["day"], "product_forecast": last.get("forecast"), "cost_ratio": ratio,
    }


def product_month(p: Dict[str, Any], m: str, cur: str) -> Optional[Dict[str, Any]]:
    """A product's revenue and linked cost for a past or the current month."""
    if p["tracking"] == "fixed":
        if m < p["start"] or (p.get("end") and m > p["end"]):
            return None
        f = p["months"].get(m)
        return {"revenue": f["revenue"] if f else p["amount"], "cost": f["cost"] if f else 0.0, "how": "final"}
    f = p["months"].get(m)
    if f:
        return {"revenue": f["revenue"], "cost": f["cost"], "how": "final"}
    figs = sorted([x for x in p["figures"] if x["day"][:7] == m], key=lambda x: x["day"])
    if not figs:
        return None
    pc = pace(m, figs)
    ratio = pc["cost_ratio"] if pc["cost_ratio"] is not None else 0.0
    return {"revenue": pc["value"], "cost": pc["value"] * ratio, "how": "estimated" if m < cur else "so_far", "pace": pc}


# ----------------------------------------------------------------- expenses

def expense_active(e: Dict[str, Any], m: str) -> bool:
    if e["how"] == "once":
        return m in e["months"]
    return m >= e["start"] and (not e.get("end") or m <= e["end"])


def expense_planned(e: Dict[str, Any], m: str, cur: str, last: str, fits: Dict[int, Fit]) -> float:
    if e["how"] == "once" or not expense_active(e, m):
        return 0.0
    if e["how"] == "monthly":
        aprils = sum(1 for x in m_range(add_m(cur, 1), m) if x[5:] == "04") if m > cur else 0
        return e["amount"] * (1 + e["raise"] / 100) ** aprils
    hist = [x for x in sorted(e["months"]) if x <= last]
    if len(hist) < 3:
        return e["amount"]
    fit = fits.get(e["id"])
    if fit is None:
        fit = fits[e["id"]] = Fit([e["months"][x] for x in hist])
    return fit.at(max(m_idx(m) - m_idx(hist[-1]), 1))["v"]


def expense_value(e: Dict[str, Any], m: str, cur: str, last: str, fits: Dict[int, Fit]) -> float:
    if m in e["months"]:
        return e["months"][m]
    if m < cur:
        return 0.0  # not entered: counted as nothing and flagged on the page
    return expense_planned(e, m, cur, last, fits)


# ----------------------------------------------------------------- the whole picture

def build(data: Dict[str, Any], today: date) -> Dict[str, Any]:
    s = data["settings"]
    start, buf = s["start_month"], 1 + s["buffer_pct"] / 100
    cur = today.isoformat()[:7]
    last = add_m(cur, -1)
    hist = m_range(start, last) if start <= last else []
    fut = m_range(cur, add_m(cur, HORIZON - 1))
    months = hist + fut
    fits: Dict[int, Fit] = {}

    # ---- products
    prods_out = []
    p_by_month: Dict[str, Dict[str, Any]] = {m: {"rev": 0.0, "cost": 0.0, "lo": 0.0, "hi": 0.0, "costs": {}} for m in months}
    for p in data["products"]:
        actual = {m: product_month(p, m, cur) for m in hist + [cur]}
        if p["tracking"] == "fixed":
            for m in months:
                live = m >= p["start"] and (not p.get("end") or m <= p["end"])
                r = (actual.get(m) or {}).get("revenue", p["amount"]) if live else 0.0
                c = (actual.get(m) or {}).get("cost", 0.0) if live else 0.0
                pm = p_by_month[m]
                pm["rev"] += r; pm["lo"] += r; pm["hi"] += r; pm["cost"] += c
            prods_out.append({"id": p["id"], "current": None, "months": _product_rows(p, actual, hist), "cost_ratio": 0.0})
            continue
        first = next((m for m in hist + [cur] if actual.get(m)), None)
        series = [(actual.get(m) or {}).get("revenue", 0.0) for m in m_range(first, cur)] if first else []
        fit = Fit(series or [0.0])
        recent = [actual[m] for m in hist[-3:] if actual.get(m)]
        rev3 = sum(a["revenue"] for a in recent)
        ratio = (sum(a["cost"] for a in recent) / rev3) if rev3 else 0.0
        if not recent and actual.get(cur) and actual[cur].get("pace", {}).get("cost_ratio") is not None:
            ratio = actual[cur]["pace"]["cost_ratio"]
        for m in months:
            pm = p_by_month[m]
            if m < cur:
                a = actual.get(m)
                if a:
                    pm["rev"] += a["revenue"]; pm["lo"] += a["revenue"]; pm["hi"] += a["revenue"]; pm["cost"] += a["cost"]
                    pm["costs"][p["id"]] = a["cost"]
                continue
            if not first or m < p["start"] or (p.get("end") and m > p["end"]):
                continue
            if m == cur and actual.get(cur):
                pc = actual[cur]["pace"]; f = {"v": pc["value"], "lo": pc["lo"], "hi": pc["hi"]}
            else:
                f = fit.at(m_idx(m) - m_idx(cur))
            pm["rev"] += f["v"]; pm["lo"] += f["lo"]; pm["hi"] += f["hi"]
            pm["cost"] += f["v"] * ratio
            pm.setdefault("ratios", []).append((f["lo"], f["v"], ratio))
            pm["costs"][p["id"]] = f["v"] * ratio
        cur_a = actual.get(cur)
        prods_out.append({
            "id": p["id"],
            "current": cur_a["pace"] if cur_a and cur_a.get("pace") else None,
            "expected_this_month": (cur_a or {}).get("revenue") if cur_a else (fit.at(0)["v"] if first else None),
            "months": _product_rows(p, actual, hist),
            "cost_ratio": ratio,
            "fit": fit.params,
        })

    # ---- expenses
    base: Dict[str, float] = {}
    by_cat: Dict[str, Dict[int, float]] = {}
    for m in months:
        base[m] = 0.0; by_cat[m] = {}
        for e in data["expenses"]:
            v = expense_value(e, m, cur, last, fits)
            base[m] += v
            by_cat[m][e["category_id"]] = by_cat[m].get(e["category_id"], 0.0) + v

    # ---- cases
    cases: Dict[str, Dict[str, Dict[str, float]]] = {"expected": {}, "slower": {}}
    for m in months:
        pm = p_by_month[m]
        if m < cur:
            row = {"inc": pm["rev"], "cost": base[m] + pm["cost"]}
            cases["expected"][m] = row; cases["slower"][m] = dict(row)
            continue
        slower_cost = sum(lo * r for lo, _, r in pm.get("ratios", []))
        fixed_cost = pm["cost"] - sum(v * r for _, v, r in pm.get("ratios", []))
        cases["expected"][m] = {"inc": pm["rev"], "cost": base[m] * buf + pm["cost"]}
        cases["slower"][m] = {"inc": pm["lo"], "cost": base[m] * (buf + .05) + slower_cost + fixed_cost}

    def cap_by(m: str) -> float:
        return sum(c["amount"] for c in data["capital"] if c["day"][:7] <= m)

    res: Dict[str, Dict[str, Any]] = {}
    for k, rows in cases.items():
        cum, cash, net = 0.0, {}, {}
        for m in months:
            cum += rows[m]["inc"] - rows[m]["cost"]
            net[m] = cum
            cash[m] = cap_by(m) + cum
        out = next((m for m in fut if cash[m] < 0), None)
        be = next((m for i, m in enumerate(fut) if all(rows[x]["inc"] >= rows[x]["cost"] for x in fut[i:i + 3])), None)
        back = next((m for m in fut if net[m] >= 0), None)
        low = min(fut, key=lambda m: cash[m])
        res[k] = {"cash": cash, "out": out, "be": be, "back": back, "low": low,
                  "runway": (m_idx(out) - m_idx(cur)) if out else None}

    E, C = res["expected"], res["slower"]
    now = cases["expected"][cur]
    gap = now["cost"] - now["inc"]
    if gap <= 0:
        state = "self_funding"
    elif not E["out"] or (E["be"] and m_idx(E["out"]) > m_idx(E["be"])):
        state = "good" if (C["runway"] is None or C["runway"] >= s["green_months"]) else ("watch" if C["runway"] >= s["amber_months"] else "risk")
    else:
        state = "risk"
    if state == "risk" and E["out"] is None:
        state = "watch"

    first_income = next((m for m in hist if cases["expected"][m]["inc"] > 0), None)
    three_ago = add_m(cur, -3)
    g3 = cases["expected"].get(three_ago)

    # ---- things that need the CEO
    todo = []
    missing = [e["name"] for e in data["expenses"] if e["how"] != "once" and expense_active(e, last) and last not in e["months"]] if hist else []
    if missing:
        todo.append({"kind": "expenses_missing", "month": last, "names": missing})
    for p, po in zip(data["products"], prods_out):
        if p["tracking"] != "figures":
            continue
        for row in po["months"]:
            if row["how"] == "estimated":
                todo.append({"kind": "product_month_estimated", "product_id": p["id"], "product": p["name"], "month": row["month"], "revenue": row["revenue"]})
        figs = sorted(x["day"] for x in p["figures"])
        live = p["start"] <= cur and (not p.get("end") or p["end"] >= cur)
        age = days_between(figs[-1], today.isoformat()) if figs else None
        if live and (age is None or age > 10):
            todo.append({"kind": "product_figure_due", "product_id": p["id"], "product": p["name"], "days": age})

    # ---- bank check
    bank = None
    if data.get("bank"):
        b = data["bank"]
        bm = b["day"][:7]
        calc = E["cash"].get(bm)
        if calc is not None:
            bank = {"day": b["day"], "amount": b["amount"], "calc": calc, "diff": b["amount"] - calc}

    all_days = [x["day"] for p in data["products"] for x in p["figures"]]
    cat_names = {c["id"]: c["name"] for c in data["categories"]}
    where = [{"name": cat_names.get(cid, "Other"), "amount": v * buf} for cid, v in by_cat[cur].items() if v > 0]
    for p in data["products"]:
        c = p_by_month[cur]["costs"].get(p["id"], 0.0)
        if c > 0:
            where.append({"name": f"{p['name']} {(p.get('cost_label') or 'costs').lower()}", "amount": c})
    where.sort(key=lambda x: -x["amount"])

    return {
        "today": today.isoformat(), "current_month": cur, "last_month": last, "start_month": start,
        "state": state,
        "cash_now": E["cash"].get(last, cap_by(last)),
        "now": {"inc": now["inc"], "cost": now["cost"], "gap": gap},
        "expected": {k: E[k] for k in ("out", "be", "back", "low", "runway")} | {"low_cash": E["cash"][E["low"]]},
        "slower": {k: C[k] for k in ("out", "be", "back", "runway")},
        "need": max(-min(E["cash"][m] for m in fut), 0.0),
        "share_now": (now["inc"] / now["cost"]) if now["cost"] else None,
        "first_income_month": first_income,
        "share_first": (cases["expected"][first_income]["inc"] / cases["expected"][first_income]["cost"]) if first_income and cases["expected"][first_income]["cost"] else None,
        "gap_change_3m": ((g3["cost"] - g3["inc"]) - gap) if g3 else None,
        "months": [{"month": m, "inc": cases["expected"][m]["inc"], "cost": cases["expected"][m]["cost"],
                    "cash": E["cash"][m], "actual": m < cur} for m in months],
        "where": where,
        "products": prods_out,
        "planned": {e["id"]: {m: expense_planned(e, m, cur, last, fits) for m in m_range(cur, add_m(cur, 12))} for e in data["expenses"]},
        "todo": todo,
        "bank": bank,
        "last_figure_day": max(all_days) if all_days else None,
        "settings": s,
    }


def _product_rows(p: Dict[str, Any], actual: Dict[str, Any], hist: List[str]) -> List[Dict[str, Any]]:
    rows = []
    first = next((m for m in hist if actual.get(m)), None)
    for m in (m_range(first, hist[-1]) if first and hist else []):
        a = actual.get(m)
        rows.append({"month": m, "revenue": a["revenue"] if a else None, "cost": a["cost"] if a else None,
                     "how": a["how"] if a else "missing"})
    return list(reversed(rows))
