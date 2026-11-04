"""The Runway forecast: plain functions over plain data, so it can be tested without a database.

Method, in short (the page's "How these numbers are worked out" says the same in plain words):

- Going out: expenses as entered. 'monthly' lines keep their amount and rise by raise_pct each
  April; 'varies' lines follow their own trend; 'once' lines only count in months they have an
  entry. A safety buffer (default 5%) is added to every month from the current one on.
- Coming in: each product separately.
  * 'fixed' products: their amount every month between start and end.
  * 'once' products (onboarding fees, set-up fees): only the months something is typed in.
  * 'figures' / 'billed' products: with fewer than MIN_TREND months of real history there is no
    trend to trust, so the recent level (last three months, latest weighted most) is carried
    forward. From MIN_TREND months on, a damped Holt trend (level + trend, the trend fading each
    month, phi at most 0.9 so early growth is not stretched over years) fitted to the months that
    really have a total. Missing months are left out, never counted as zero. A billed product
    never goes below its live clients' minimums.
  * A figure typed into a future month is taken as the plan for that month (any product).
    The current month comes from the "so far" figures: money so far / days, the latest stretch
    weighted up to 60% (less when it covers under a week), averaged with the product's own
    month-end forecast when one was typed in.
  * A product's linked cost (bank charges for GMDI) is counted as going out, at the same share
    of revenue as its last three months.
- Three cases: expected (middle of the forecast), slower (low end of the 80% range for income,
  costs +5% more) and faster (high end of income). "On track" is judged on the slower case on
  purpose; the break-even range shown is faster .. slower.
- Confidence (0-100) says how much to trust all this: months of history, how steady income is,
  how much future income is already committed, whether expenses are filled in, how fresh the
  figures are. Each missing piece is listed so it can be fixed.
- Expenses 'varies' with under three months typed repeat the last typed figure (like Excel's
  "same as last month"), falling back to the usual amount.
"""
from __future__ import annotations

import calendar
import math
from datetime import date
from typing import Any, Dict, List, Optional

Z80 = 1.2816
HORIZON = 36  # months shown ahead, current month included
MIN_TREND = 6  # months of real history before a growth trend is trusted


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
    def __init__(self, y: List[float], min_trend: int = 3):
        self.flat = len(y) < min_trend
        if self.flat:
            # Not enough history for a trend: carry the recent level forward (last three months,
            # the latest weighted most) and say plainly that it is a level, not a growth curve.
            tail = y[-3:]
            w = list(range(1, len(tail) + 1))
            self.mean = sum(v * k for v, k in zip(tail, w)) / sum(w) if tail else 0.0
            rng = (max(tail) - min(tail)) / 2 if len(tail) > 1 else 0.0
            self.spread = max(rng, self.mean * 0.25)
            self.params = None
            return
        best = None
        for a in (.2, .3, .4, .5, .6, .7, .8, .9):
            for b in (.05, .1, .2, .3, .4):
                for phi in (.8, .85, .9):
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


def committed(p: Dict[str, Any], m: str) -> float:
    """What a billed product's clients must pay for month m at the least: each client's minimum
    monthly bill plus its fixed monthly charges, for clients live that month."""
    return sum(c["minimum"] + (c.get("fixed") or 0.0) for c in p.get("clients", [])
               if c["start"] <= m and (not c.get("end") or m <= c["end"]))


def billed_month(p: Dict[str, Any], m: str, cur: str) -> Optional[Dict[str, Any]]:
    """A billed-monthly product (GMDI) for a past or the current month.

    The final invoice total wins. Otherwise the latest figure that month: the product's own
    expected bill (GMDI's Profit Outlook, minimums already applied) is taken as it is; usage so far
    alone is carried to the month end and lifted to the clients' committed minimum, since a client
    below its minimum is billed the minimum.
    """
    f = p["months"].get(m)
    if f:
        return {"revenue": f["revenue"], "cost": f["cost"], "how": "final"}
    figs = sorted([x for x in p["figures"] if x["day"][:7] == m], key=lambda x: x["day"])
    if not figs:
        return None
    last = figs[-1]
    floor = committed(p, m)
    if last.get("forecast"):
        bill = last["forecast"]
        source = "product"
        lo, hi = bill * .95, bill * 1.05
    else:
        usage = pace(m, [x for x in figs if x["revenue"]]) if any(x["revenue"] for x in figs) else None
        bill = max(usage["value"] if usage else 0.0, floor)
        lo, hi = max(usage["lo"] if usage else 0.0, floor), max(usage["hi"] if usage else 0.0, floor)
        source = "usage"
    cost = last["cost"] if last.get("cost") is not None else None
    day = int(last["day"][8:])
    cur_info = {"value": bill, "lo": lo, "hi": hi, "source": source, "committed": floor, "as_of": last["day"],
                "day": day, "days": days_in(m), "so_far": last["revenue"] or 0.0, "product_forecast": last.get("forecast"),
                "expected_cost": cost, "cost_ratio": (cost / bill) if cost is not None and bill else None}
    return {"revenue": bill, "cost": cost if cost is not None else 0.0, "cost_known": cost is not None,
            "how": "estimated" if m < cur else "so_far", "pace": cur_info}


def product_month(p: Dict[str, Any], m: str, cur: str) -> Optional[Dict[str, Any]]:
    """A product's revenue and linked cost for a past or the current month."""
    if p["tracking"] == "once":
        f = p["months"].get(m)
        return {"revenue": f["revenue"], "cost": f["cost"], "how": "final"} if f else None
    if p["tracking"] == "billed":
        return billed_month(p, m, cur)
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
        # Like Excel's "same as last month": the latest figure typed, else the usual amount.
        return e["months"][hist[-1]] if hist else e["amount"]
    fit = fits.get(e["id"])
    if fit is None:
        fit = fits[e["id"]] = Fit([e["months"][x] for x in hist])
    return fit.at(max(m_idx(m) - m_idx(hist[-1]), 1))["v"]


def expense_value(e: Dict[str, Any], m: str, cur: str, last: str, fits: Dict[int, Fit]) -> float:
    if m in e["months"]:
        return e["months"][m]
    if m < cur:
        # "Every month, same amount" means exactly that, past months included, unless a month
        # was entered differently. Only lines whose amount changes need each month typed in;
        # until then they count as nothing and are flagged on the page.
        return e["amount"] if e["how"] == "monthly" and expense_active(e, m) else 0.0
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
    # Each product gives, per billing month, its revenue (expected and the low/high range) and its
    # linked cost. Revenue reaches the bank pay_delay months later (clients pay the bill after the
    # month ends); the linked cost, like bank charges, is paid in the month itself.
    prods_out = []
    p_by_month: Dict[str, Dict[str, Any]] = {m: {"rev": 0.0, "cost": 0.0, "lo": 0.0, "hi": 0.0, "cost_lo": 0.0, "costs": {}} for m in months}
    for p in data["products"]:
        delay = int(p.get("pay_delay") or 0)
        actual = {m: product_month(p, m, cur) for m in hist + [cur]}
        bill: Dict[str, Dict[str, float]] = {}
        out: Dict[str, Any] = {"id": p["id"], "pay_delay": delay, "months": _product_rows(p, actual, hist), "cost_ratio": 0.0, "current": None}
        how: Dict[str, str] = {}
        if p["tracking"] in ("fixed", "once"):
            for m in months:
                typed = p["months"].get(m)
                if p["tracking"] == "once":
                    if not typed:
                        continue
                    r, c = typed["revenue"], typed["cost"]
                else:
                    live = m >= p["start"] and (not p.get("end") or m <= p["end"])
                    if not live and not typed:
                        continue
                    r = typed["revenue"] if typed else p["amount"]
                    c = typed["cost"] if typed else 0.0
                bill[m] = {"v": r, "lo": r, "hi": r, "cost": c, "cost_lo": c}
                how[m] = "typed" if typed else "rule"
        else:
            billed = p["tracking"] == "billed"
            first = next((m for m in hist + [cur] if actual.get(m)), None)
            # Only months that really have a figure; a month nobody filled in is not a zero.
            series = [actual[m]["revenue"] for m in m_range(first, cur) if actual.get(m)] if first else []
            fit = Fit(series or [0.0], MIN_TREND)
            out["history_months"] = sum(1 for m in hist if actual.get(m) and actual[m]["how"] == "final")
            out["trend"] = not fit.flat
            recent = [actual[m] for m in hist[-3:] if actual.get(m) and actual[m].get("cost_known", True)]
            rev3 = sum(x["revenue"] for x in recent)
            ratio = (sum(x["cost"] for x in recent) / rev3) if rev3 else 0.0
            if not recent and actual.get(cur) and (actual[cur].get("pace") or {}).get("cost_ratio") is not None:
                ratio = actual[cur]["pace"]["cost_ratio"]
            out["cost_ratio"] = ratio
            for m in months:
                live = m >= p["start"] and (not p.get("end") or m <= p["end"])
                floor = committed(p, m) if billed else 0.0
                if m < cur:
                    x = actual.get(m)
                    if x:
                        c = x["cost"] if x.get("cost_known", True) else x["revenue"] * ratio
                        bill[m] = {"v": x["revenue"], "lo": x["revenue"], "hi": x["revenue"], "cost": c, "cost_lo": c}
                        how[m] = "typed" if x["how"] == "final" else "estimated"
                    continue
                typed = p["months"].get(m) if m > cur else None
                if typed:
                    r = typed["revenue"]
                    c = typed["cost"] if typed["cost"] else r * ratio
                    bill[m] = {"v": r, "lo": r, "hi": r, "cost": c, "cost_lo": c}
                    how[m] = "typed"
                    continue
                if not live or (not first and not floor):
                    continue
                if m == cur and actual.get(cur):
                    pc = actual[cur]["pace"]; f = {"v": pc["value"], "lo": pc["lo"], "hi": pc["hi"]}
                    known = (pc.get("expected_cost") if billed else None)
                elif first:
                    f = fit.at(m_idx(m) - m_idx(cur)); known = None
                else:
                    f = {"v": 0.0, "lo": 0.0, "hi": 0.0}; known = None
                # a billed client never pays less than its minimum
                f = {k: max(v, floor) for k, v in f.items()}
                c = known if known is not None else f["v"] * ratio
                bill[m] = {"v": f["v"], "lo": f["lo"], "hi": f["hi"], "cost": c, "cost_lo": known if known is not None else f["lo"] * ratio,
                           "committed": floor}
                how[m] = ("typed" if (actual.get(cur) or {}).get("how") == "final" else "so_far") if m == cur and actual.get(cur) else ("minimum" if floor and f["v"] <= floor else "forecast")
            cur_a = actual.get(cur)
            out.update({
                "current": cur_a["pace"] if cur_a and cur_a.get("pace") else None,
                "expected_this_month": (bill.get(cur) or {}).get("v"),
                "fit": fit.params,
            })
            if billed:
                out.update({"committed": committed(p, cur), "committed_next": committed(p, add_m(cur, 1)),
                            "clients_live": sum(1 for c in p.get("clients", []) if c["start"] <= cur and (not c.get("end") or cur <= c["end"]))})
        for m in months:
            b = bill.get(m)
            if b:
                pm = p_by_month[m]
                pm["cost"] += b["cost"]; pm["cost_lo"] += b["cost_lo"]
                if b["cost"]:
                    pm["costs"][p["id"]] = b["cost"]
        # Money that reaches the bank. Expected: the bill pay_delay months back. Received: what the CEO
        # typed for a past month (a client may pay less, late, or ahead). The difference is carried:
        # still owed is expected in the current month; paid ahead comes off the next months.
        received = p.get("received") or {}
        owed = 0.0
        cash_rows: Dict[str, Dict[str, Any]] = {}
        for m in months:
            due = bill.get(add_m(m, -delay)) if delay else bill.get(m)
            exp_v = due["v"] if due else 0.0
            lo, hi = (due["lo"], due["hi"]) if due else (0.0, 0.0)
            if m < cur:
                got = received.get(m)
                cash = got if got is not None else exp_v
                owed += exp_v - cash
                lo = hi = cash
                cash_rows[m] = {"expected": exp_v, "received": got, "cash": cash, "owed_after": owed}
            else:
                carry = owed
                cash = max(exp_v + carry, 0.0)
                used = cash - exp_v                         # how much of the carry this month absorbs
                owed = carry - used
                lo, hi = max(lo + used, 0.0), max(hi + used, 0.0)
                got = received.get(m)
                if got is not None and got > cash:          # already received more this month than expected
                    owed -= got - cash
                    cash = lo = hi = got
                cash_rows[m] = {"expected": exp_v, "received": got, "cash": cash, "owed_after": owed}
            if cash or lo or hi:
                pm = p_by_month[m]
                pm["rev"] += cash; pm["lo"] += lo; pm["hi"] += hi
        out["cash"] = cash_rows
        last_closed = add_m(cur, -1)
        out["owed_now"] = (cash_rows.get(last_closed) or {}).get("owed_after", 0.0)
        out["grid"] = {m: {"v": b["v"], "cost": b["cost"], "how": how.get(m, "forecast")} for m, b in bill.items()}
        out["future_split"] = {"committed": sum(min(bill[m].get("committed", bill[m]["v"] if how.get(m) in ("typed", "rule") else 0.0), bill[m]["v"])
                                                for m in fut[:6] if m in bill),
                               "total": sum(bill[m]["v"] for m in fut[:6] if m in bill)}
        out["paid_this_month"] = (cash_rows.get(cur) or {}).get("cash", 0.0)
        out["billed_this_month"] = (bill.get(cur) or {}).get("v", 0.0)
        prods_out.append(out)

    # ---- expenses
    base: Dict[str, float] = {}
    by_cat: Dict[str, Dict[int, float]] = {}
    exp_grid: Dict[int, Dict[str, Dict[str, Any]]] = {e["id"]: {} for e in data["expenses"]}
    for m in months:
        base[m] = 0.0; by_cat[m] = {}
        for e in data["expenses"]:
            v = expense_value(e, m, cur, last, fits)
            base[m] += v
            by_cat[m][e["category_id"]] = by_cat[m].get(e["category_id"], 0.0) + v
            if v or m in e["months"]:
                exp_grid[e["id"]][m] = {"v": v, "how": "typed" if m in e["months"] else "rule"}

    # ---- cases
    cases: Dict[str, Dict[str, Dict[str, float]]] = {"expected": {}, "slower": {}, "faster": {}}
    for m in months:
        pm = p_by_month[m]
        if m < cur:
            row = {"inc": pm["rev"], "cost": base[m] + pm["cost"]}
            cases["expected"][m] = row; cases["slower"][m] = dict(row); cases["faster"][m] = dict(row)
            continue
        cases["expected"][m] = {"inc": pm["rev"], "cost": base[m] * buf + pm["cost"]}
        cases["slower"][m] = {"inc": pm["lo"], "cost": base[m] * (buf + .05) + pm["cost_lo"]}
        cases["faster"][m] = {"inc": pm["hi"], "cost": base[m] * buf + pm["cost"] * (pm["hi"] / pm["rev"] if pm["rev"] else 1)}

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

    E, C, Fa = res["expected"], res["slower"], res["faster"]
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
    missing = [e["name"] for e in data["expenses"] if e["how"] == "varies" and expense_active(e, last) and last not in e["months"]] if hist else []
    if missing:
        todo.append({"kind": "expenses_missing", "month": last, "names": missing})
    for p, po in zip(data["products"], prods_out):
        if p["tracking"] in ("fixed", "once"):
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
        "faster": {k: Fa[k] for k in ("out", "be", "back", "runway")},
        "confidence": confidence(data, prods_out, hist, cur, last, today, E, C, Fa, cases),
        "grid": {
            "expenses": exp_grid,
            "capital": {m: sum(c["amount"] for c in data["capital"] if c["day"][:7] == m) for m in months},
            "buffer": {m: base[m] * (buf - 1) for m in fut},
            "income_total": {m: cases["expected"][m]["inc"] for m in months},
            "cost_total": {m: cases["expected"][m]["cost"] for m in months},
            "cash": {m: E["cash"][m] for m in months},
        },
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


# ----------------------------------------------------------------- confidence

def confidence(data, prods_out, hist, cur, last, today, E, C, Fa, cases) -> Dict[str, Any]:
    """How much to trust the forecast, 0-100, and what would make it better.

    Five parts, each 0..1, weighted:
      history 30   months of real income totals (6+ is full marks: enough to see a trend)
      steady 15    how much month-to-month income moves around the trend (small = good)
      committed 20 share of the next 6 months' income already fixed (client minimums, retainers,
                   typed plans) rather than guessed from the trend
      expenses 20  expense lines that change month to month and are filled in for recent months
      fresh 15     product figures from the last 10 days and a bank check from the last 45
    """
    gaps: List[Dict[str, str]] = []
    earning = [(p, po) for p, po in zip(data["products"], prods_out) if p["tracking"] in ("figures", "billed")]

    n = max([po.get("history_months", 0) for _, po in earning] or [0])
    history = min(n / MIN_TREND, 1.0)
    if earning and n < MIN_TREND:
        gaps.append({"key": "history", "text": f"Only {n} month{'s' if n != 1 else ''} of real income totals. Six or more are needed "
                     f"before a growth trend can be trusted, so for now the forecast assumes income stays near its recent level.",
                     "fix": "Type each past month's final total into the sheet."})

    # steadiness: relative change between consecutive real months, beyond the average change
    vals = []
    for p, po in earning:
        rows = [r["revenue"] for r in reversed(po["months"]) if r["how"] == "final" and r["revenue"]]
        vals.append(rows)
    longest = max(vals, key=len) if vals else []
    if len(longest) >= 3:
        steps = [(b - a) / a for a, b in zip(longest, longest[1:]) if a]
        mean = sum(steps) / len(steps)
        dev = math.sqrt(sum((x - mean) ** 2 for x in steps) / len(steps))
        steady = max(0.0, 1 - dev / 0.5)
        if steady < .5:
            gaps.append({"key": "steady", "text": "Income jumps around a lot from month to month, so any single month ahead is a rough guess.",
                         "fix": "Nothing to fill in; more months will steady it."})
    else:
        steady = 0.3

    tot = sum(po.get("future_split", {}).get("total", 0.0) for po in prods_out)
    com = sum(po.get("future_split", {}).get("committed", 0.0) for po in prods_out)
    committed = (com / tot) if tot else 0.0
    if tot and committed < .5:
        gaps.append({"key": "committed", "text": f"Only {round(committed * 100)}% of the next six months' income is committed (client minimums, "
                     "retainers or figures you typed). The rest is a guess from past months.",
                     "fix": "Add clients' minimums, signed deals and expected onboarding or set-up fees as their own lines."})

    varies = [e for e in data["expenses"] if e["how"] == "varies" and e["start"] <= last and (not e.get("end") or e["end"] >= last)]
    recent = [m for m in hist[-3:]]
    if varies and recent:
        filled = sum(1 for e in varies for m in recent if m >= e["start"] and m in e["months"])
        want = sum(1 for e in varies for m in recent if m >= e["start"])
        expenses = filled / want if want else 1.0
        if expenses < 1:
            names = sorted({e["name"] for e in varies for m in recent if m >= e["start"] and m not in e["months"]})
            gaps.append({"key": "expenses", "text": f"Expenses that change month to month are missing for recent months: {', '.join(names[:5])}"
                         f"{' and more' if len(names) > 5 else ''}.", "fix": "Fill those cells in the sheet."})
    else:
        expenses = 1.0 if data["expenses"] else 0.0
    if not data["expenses"]:
        gaps.append({"key": "expenses", "text": "No expenses yet, so the money going out is unknown.", "fix": "Add salaries and other costs in the sheet."})
    salary_like = [e for e in data["expenses"] if "salar" in e["name"].lower()]
    if data["expenses"] and not salary_like:
        gaps.append({"key": "salaries", "text": "No line has 'salary' in its name. Salaries are usually the biggest cost, so check they are all in.",
                     "fix": "Add one line per person or one line per team."})

    days = [x["day"] for p in data["products"] for x in p["figures"]]
    age = days_between(max(days), today.isoformat()) if days else None
    bank = data.get("bank")
    bank_age = days_between(bank["day"], today.isoformat()) if bank else None
    fresh = (0.6 if age is not None and age <= 10 else 0.0) + (0.4 if bank_age is not None and bank_age <= 45 else 0.0)
    if earning and (age is None or age > 10):
        gaps.append({"key": "fresh", "text": "No income figure in the last 10 days.", "fix": "Type this month's figure so far."})
    if bank_age is None or bank_age > 45:
        gaps.append({"key": "bank", "text": "The bank balance hasn't been checked in the last 45 days, so the starting cash may be off.",
                     "fix": "Enter the balance from the latest bank statement."})

    parts = {"history": history, "steady": steady, "committed": committed, "expenses": expenses, "fresh": fresh}
    weights = {"history": 30, "steady": 15, "committed": 20, "expenses": 20, "fresh": 15}
    score = round(sum(parts[k] * weights[k] for k in parts))
    label = "high" if score >= 70 else "medium" if score >= 45 else "low"
    return {"score": score, "label": label, "parts": {k: round(v, 2) for k, v in parts.items()}, "weights": weights,
            "be_range": {"early": Fa["be"], "expected": E["be"], "late": C["be"]},
            "out_range": {"early": C["out"], "expected": E["out"], "late": Fa["out"]},
            "gaps": gaps}


def _product_rows(p: Dict[str, Any], actual: Dict[str, Any], hist: List[str]) -> List[Dict[str, Any]]:
    rows = []
    first = next((m for m in hist if actual.get(m)), None)
    for m in (m_range(first, hist[-1]) if first and hist else []):
        a = actual.get(m)
        rows.append({"month": m, "revenue": a["revenue"] if a else None, "cost": a["cost"] if a else None,
                     "how": a["how"] if a else "missing"})
    return list(reversed(rows))
