"""Bulk upload for Runway: CSV templates in, a checked plan out.

Two templates, both downloadable pre-filled with what is already entered (so downloading,
editing in Excel and uploading again is safe: unchanged cells change nothing):

1. Monthly history (one row per line, one column per month), for back-filling from Dec 2025:
     Type, Name, Category, How often, Yearly raise %, Dec 2025, Jan 2026, ...
   Type is Expense, Income (a product's revenue for the month, before GST),
   Income cost (that product's linked cost, e.g. bank charges; Category = the cost's name)
   or Capital (money put in, counted on the 1st of the month; Name = a note).
2. Product figures (one row per figure), for "revenue so far this month" taken on any day:
     Product, Date, Revenue so far this month, Cost so far this month, Product's month-end forecast

Nothing here touches the database. parse() turns the file into a plan (what will be created,
which cells will change, and every problem with its row and column); the endpoint shows the
plan as a preview and applies it only when the CEO confirms and the plan has no errors.

Excel is forgiving and so is this: a BOM, ; or tab separators, headers Excel turned into dates
(Dec-25, 01-12-2025), amounts like ₹1,50,000 / 1.5L / 2 Cr, and "#" instruction rows are fine.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from app.services.runway_forecast import add_m, m_range

MAX_BYTES = 1_000_000
MAX_ROWS = 3000
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

TYPES = {
    "expense": "expense", "expenses": "expense", "cost": "expense", "spend": "expense",
    "income": "income", "revenue": "income", "sales": "income",
    "income cost": "income_cost", "product cost": "income_cost", "cost of income": "income_cost", "linked cost": "income_cost",
    "capital": "capital", "money put in": "capital", "investment": "capital", "funding": "capital",
}
HOW = {
    "every month": "monthly", "every month, same amount": "monthly", "same every month": "monthly", "monthly": "monthly", "fixed": "monthly", "same": "monthly",
    "changes monthly": "varies", "every month, amount changes": "varies", "changes": "varies", "varies": "varies", "variable": "varies",
    "one-time": "once", "one time": "once", "once": "once", "just once": "once", "one off": "once", "one-off": "once",
}
HOW_LABEL = {"monthly": "Every month", "varies": "Changes monthly", "once": "One-time"}


def month_label(m: str) -> str:
    return f"{MN[int(m[5:]) - 1]} {m[:4]}"


# ----------------------------------------------------------------- reading cells

def _year(y: str) -> Optional[int]:
    if not y.isdigit():
        return None
    y4 = int(y) + 2000 if len(y) == 2 else int(y)
    return y4 if 2000 <= y4 <= 2100 else None


def parse_month(s: str) -> Optional[str]:
    """'2025-12', 'Dec 2025', 'December 2025', 'Dec-25', "Dec'25", '12/2025', '01-12-2025', '2025-12-01' → '2025-12'."""
    t = (s or "").strip().lower().replace("’", "'")
    if not t:
        return None
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})(?:[-/.]\d{1,2})?", t)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        return f"{y}-{mo:02d}" if 1 <= mo <= 12 and 2000 <= y <= 2100 else None
    m = re.fullmatch(r"(?:(\d{1,2})[-/.])?(\d{1,2})[-/.](\d{2}|\d{4})", t)          # [DD/]MM/YYYY, Indian order
    if m:
        mo, y = int(m.group(2)), _year(m.group(3))
        return f"{y}-{mo:02d}" if y and 1 <= mo <= 12 else None
    m = re.fullmatch(r"(?:\d{1,2}[\s\-/.])?([a-z]{3,9})[\s\-/.,']*(\d{2}|\d{4})", t)   # [1-]Dec[-/ ']25
    if m and m.group(1)[:3] in MONTHS:
        y = _year(m.group(2))
        return f"{y}-{MONTHS.index(m.group(1)[:3]) + 1:02d}" if y else None
    return None


def parse_day(s: str) -> Optional[str]:
    """'2026-10-16', '16/10/2026', '16-10-26', '16 Oct 2026', '16-Oct-26' → '2026-10-16' (day first, as in India)."""
    t = (s or "").strip().lower()
    for pat, order in ((r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", "ymd"), (r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})", "dmy")):
        m = re.fullmatch(pat, t)
        if m:
            a, b, c = m.groups()
            y, mo, d = (int(a), int(b), int(c)) if order == "ymd" else (_year(c), int(b), int(a))
            return _safe_day(y, mo, d)
    m = re.fullmatch(r"(\d{1,2})[\s\-/.]+([a-z]{3,9})[\s\-/.,']+(\d{2}|\d{4})", t)
    if m and m.group(2)[:3] in MONTHS:
        return _safe_day(_year(m.group(3)), MONTHS.index(m.group(2)[:3]) + 1, int(m.group(1)))
    return None


def _safe_day(y, mo, d) -> Optional[str]:
    try:
        return date(y, mo, d).isoformat() if y else None
    except (TypeError, ValueError):
        return None


def parse_amount(s: str) -> Tuple[Optional[float], Optional[str]]:
    """Returns (amount or None for an empty cell, error message or None)."""
    t = (s or "").strip().lower()
    if t in ("", "-", "—", "–", "na", "n/a", "nil"):
        return None, None
    t = re.sub(r"^(₹|rs\.?|inr)\s*", "", t).replace(",", "").replace(" ", "").replace("₹", "")
    mult = 1
    for suf, k in (("crores", 1e7), ("crore", 1e7), ("cr", 1e7), ("lakhs", 1e5), ("lakh", 1e5), ("lac", 1e5), ("l", 1e5), ("k", 1e3)):
        if t.endswith(suf) and t[:-len(suf)]:
            t, mult = t[:-len(suf)], k
            break
    if t.startswith("(") and t.endswith(")"):
        return None, "is negative"
    try:
        v = float(t) * mult
    except ValueError:
        return None, f"“{s.strip()}” is not an amount"
    if v < 0:
        return None, "is negative"
    if v > 1e12:
        return None, "is too large"
    return round(v, 2), None


def read_rows(text: str) -> List[List[str]]:
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = []
    for r in csv.reader(io.StringIO(text), dialect):
        if not any(c.strip() for c in r):
            continue
        if r and r[0].strip().startswith("#"):
            continue
        rows.append([c.strip() for c in r])
    return rows


# ----------------------------------------------------------------- templates

def template_monthly(data: Dict[str, Any], today: date) -> str:
    start = data["settings"]["start_month"]
    last = add_m(today.isoformat()[:7], -1)
    months = m_range(start, last) if start <= last else [today.isoformat()[:7]]
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# Runway: monthly history. One row per expense, product or capital entry; one column per month. Amounts in rupees."])
    w.writerow(["# Type = Expense / Income / Income cost / Capital. How often = Every month / Changes monthly / One-time. Leave a cell empty if there was nothing."])
    w.writerow(["# Rows already filled are what Runway has now: change them, add new rows below, then upload this file. Lines starting with # are ignored."])
    w.writerow(["Type", "Name", "Category", "How often", "Yearly raise %"] + [month_label(m) for m in months])
    cats = {c["id"]: c["name"] for c in data["categories"]}
    fmt = lambda v: "" if v is None else (str(int(v)) if float(v).is_integer() else str(v))
    for e in data["expenses"]:
        w.writerow(["Expense", e["name"], cats.get(e["category_id"], ""), HOW_LABEL[e["how"]], fmt(e["raise"]) if e["how"] == "monthly" else ""]
                   + [fmt(e["months"].get(m)) for m in months])
    for p in data["products"]:
        if p["tracking"] != "figures":
            continue
        w.writerow(["Income", p["name"], "", "", ""] + [fmt((p["months"].get(m) or {}).get("revenue")) for m in months])
        if p.get("cost_label"):
            w.writerow(["Income cost", p["name"], p["cost_label"], "", ""] + [fmt((p["months"].get(m) or {}).get("cost")) for m in months])
    for c in data["capital"]:
        m = c["day"][:7]
        if m in months:
            w.writerow(["Capital", c.get("note") or "Capital", "", "", ""] + [fmt(c["amount"]) if x == m else "" for x in months])
    if not data["expenses"]:
        w.writerow(["Expense", "Example: Tech lead salary", "IT", "Every month", "8"] + [""] * len(months))
    return out.getvalue()


def template_figures(data: Dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# Runway: product figures. One row each time you looked at a product's dashboard: the date and its revenue so far that month."])
    w.writerow(["# Dates as 16/10/2026 or 2026-10-16. Any gap between dates is fine. Lines starting with # are ignored."])
    w.writerow(["Product", "Date", "Revenue so far this month", "Cost so far this month", "Product's month-end forecast"])
    fmt = lambda v: "" if v is None else (str(int(v)) if float(v).is_integer() else str(v))
    for p in data["products"]:
        if p["tracking"] != "figures":
            continue
        for f in p["figures"]:
            w.writerow([p["name"], f["day"], fmt(f["revenue"]), fmt(f["cost"]), fmt(f["forecast"])])
    return out.getvalue()


# ----------------------------------------------------------------- parsing into a plan

def _infer_how(values: Dict[str, float], months: List[str]) -> str:
    filled = [m for m in months if m in values]
    if len(filled) <= 2 and (len(filled) < 2 or months.index(filled[1]) - months.index(filled[0]) > 1):
        return "once"
    return "monthly" if len({values[m] for m in filled}) == 1 else "varies"


def parse(text: str, data: Dict[str, Any], today: date) -> Dict[str, Any]:
    plan: Dict[str, Any] = {"kind": None, "errors": [], "warnings": [], "categories": [], "expenses": [], "products": [], "cells": []}
    err = lambda row, col, msg: plan["errors"].append({"row": row, "column": col, "message": msg})
    if len(text.encode("utf-8", "ignore")) > MAX_BYTES:
        err(None, None, "The file is larger than 1 MB. Split it into smaller files.")
        return plan
    rows = read_rows(text)
    if not rows:
        err(None, None, "The file is empty.")
        return plan
    if len(rows) > MAX_ROWS:
        err(None, None, f"The file has more than {MAX_ROWS} rows. Split it into smaller files.")
        return plan
    head = [h.lower() for h in rows[0]]
    if "date" in head and any("revenue" in h for h in head):
        plan["kind"] = "figures"
        _parse_figures(rows, data, today, plan, err)
    elif "type" in head and "name" in head:
        plan["kind"] = "monthly"
        _parse_monthly(rows, data, today, plan, err)
    else:
        err(1, None, "This does not look like a Runway template. Download one of the templates and fill that in.")
    plan["summary"] = summarize(plan)
    return plan


def _parse_monthly(rows, data, today, plan, err) -> None:
    head = rows[0]
    low = [h.lower() for h in head]
    col = lambda *names: next((i for i, h in enumerate(low) if h in names), None)
    c_type, c_name = col("type"), col("name")
    c_cat, c_how, c_raise = col("category", "group"), col("how often", "frequency", "how"), col("yearly raise %", "yearly raise", "raise %", "raise")
    start = data["settings"]["start_month"]
    cur = today.isoformat()[:7]
    month_cols: List[Tuple[int, str]] = []
    for i, h in enumerate(head):
        if i in (c_type, c_name, c_cat, c_how, c_raise):
            continue
        m = parse_month(h)
        if m:
            month_cols.append((i, m))
        elif h:
            plan["warnings"].append(f"Column “{h}” was ignored (not a month).")
    if not month_cols:
        err(1, None, "No month columns found. Month headings should look like Dec 2025 or 2025-12.")
        return
    seen_m = {}
    for i, m in month_cols:
        if m in seen_m:
            err(1, head[i], f"{month_label(m)} appears twice in the headings.")
        seen_m[m] = i
    file_months = sorted(m for _, m in month_cols)

    exp_by_name = {e["name"].strip().lower(): e for e in data["expenses"]}
    cat_by_name = {c["name"].strip().lower(): c for c in data["categories"]}
    prod_by_name = {p["name"].strip().lower(): p for p in data["products"]}
    seen_rows: Dict[Tuple[str, str], int] = {}
    new_cats: Dict[str, str] = {}
    new_prods: Dict[str, Dict[str, Any]] = {}
    income: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for r_i, row in enumerate(rows[1:], start=2):
        cell = lambda c: row[c].strip() if c is not None and c < len(row) else ""
        typ_raw, name = cell(c_type), cell(c_name)
        typ = TYPES.get(typ_raw.lower())
        if not typ:
            err(r_i, "Type", f"“{typ_raw}” is not a type. Use Expense, Income, Income cost or Capital." if typ_raw else "Type is empty.")
            continue
        if not name and typ != "capital":
            err(r_i, "Name", "Name is empty.")
            continue
        name = name or "Capital"
        if name.lower().startswith("example:"):
            continue
        key = (typ, name.lower())
        if typ != "capital" and key in seen_rows:
            err(r_i, "Name", f"“{name}” is already on row {seen_rows[key]}. Each line should appear once.")
            continue
        seen_rows[key] = r_i
        values: Dict[str, float] = {}
        bad = False
        for ci, m in month_cols:
            v, problem = parse_amount(cell(ci))
            if problem:
                err(r_i, month_label(m), f"{name}, {month_label(m)}: the amount {problem}.")
                bad = True
            elif v is not None:
                if m < start:
                    err(r_i, month_label(m), f"{month_label(m)} is before the books start ({month_label(start)}). Change the start in Runway settings first, or remove that column.")
                    bad = True
                values[m] = v
        if bad:
            continue

        if typ == "expense":
            existing = exp_by_name.get(name.lower())
            how_raw, raise_raw, cat_name = cell(c_how), cell(c_raise), cell(c_cat)
            how = HOW.get(how_raw.lower()) if how_raw else None
            if how_raw and not how:
                err(r_i, "How often", f"“{how_raw}” is not understood. Use Every month, Changes monthly or One-time.")
                continue
            raise_pct = 0.0
            if raise_raw:
                rv, problem = parse_amount(raise_raw.rstrip("%"))
                if problem or rv is None or rv > 100:
                    err(r_i, "Yearly raise %", f"“{raise_raw}” is not a percentage.")
                    continue
                raise_pct = rv
            if not existing:
                if not values:
                    continue  # a new line with no amounts: nothing to add
                if not cat_name:
                    err(r_i, "Category", f"“{name}” is new, so it needs a category (for example IT or Admin).")
                    continue
                if cat_name.lower() not in cat_by_name and cat_name.lower() not in new_cats:
                    new_cats[cat_name.lower()] = cat_name
                    plan["categories"].append(cat_name)
                how = how or _infer_how(values, file_months)
                filled = sorted(values)
                last_col = file_months[-1]
                end = filled[-1] if how != "once" and filled[-1] < last_col and filled[-1] < add_m(cur, -1) else None
                if how != "once" and any(m >= cur for m in filled):
                    end = None
                plan["expenses"].append({"row": r_i, "name": name, "category": cat_name, "how": how, "raise_pct": raise_pct if how == "monthly" else 0,
                                         "amount": values[filled[-1]] if how != "once" else 0, "start_month": filled[0] if how != "once" else None, "end_month": end})
            elif how and how != existing["how"]:
                plan["warnings"].append(f"Row {r_i}: “{name}” is already set up as {HOW_LABEL[existing['how']]}; only its monthly figures were taken. Edit it in Runway to change that.")
            for m, v in values.items():
                old = existing["months"].get(m) if existing else None
                plan["cells"].append({"target": "expense", "row": r_i, "name": name, "month": m, "amount": v, "old": old})

        elif typ in ("income", "income_cost"):
            p = prod_by_name.get(name.lower())
            if p and p["tracking"] == "fixed":
                err(r_i, "Name", f"“{name}” has a fixed monthly amount, so it takes no monthly figures. Edit it in Runway instead.")
                continue
            future = [m for m in values if m >= cur]
            if future:
                err(r_i, month_label(min(future)), f"{name}: income can only be entered for months that are over. For this month, add a figure on the Income page.")
                continue
            if not p and name.lower() not in new_prods:
                if not values:
                    continue
                new_prods[name.lower()] = {"row": r_i, "name": name, "cost_label": None, "start_month": min(values)}
            if typ == "income_cost":
                label = cell(c_cat) or (p.get("cost_label") if p else None) or "Costs"
                if not p:
                    new_prods[name.lower()]["cost_label"] = label
                elif not p.get("cost_label"):
                    plan["warnings"].append(f"Row {r_i}: “{name}” will now count “{label}” as its linked cost.")
                    plan.setdefault("product_labels", {})[p["name"]] = label
            for m, v in values.items():
                slot = income.setdefault((name.lower(), m), {"name": p["name"] if p else name, "month": m, "row": r_i})
                slot["revenue" if typ == "income" else "cost"] = v
                if typ == "income" and new_prods.get(name.lower()) and m < new_prods[name.lower()]["start_month"]:
                    new_prods[name.lower()]["start_month"] = m

        else:  # capital
            if len(values) == 0:
                continue
            for m, v in values.items():
                old = next((c["amount"] for c in data["capital"] if c["day"][:7] == m and (c.get("note") or "Capital").strip().lower() == name.lower()), None)
                plan["cells"].append({"target": "capital", "row": r_i, "name": name, "month": m, "amount": v, "old": old})

    plan["products"] = list(new_prods.values())
    for (pname, m), slot in income.items():
        p = prod_by_name.get(pname)
        old = (p["months"].get(m) if p else None) or {}
        if "revenue" not in slot and not old:
            err(slot["row"], month_label(m), f"{slot['name']}, {month_label(m)}: there is a cost but no revenue for that month. Add the Income row's figure too.")
            continue
        plan["cells"].append({"target": "product_month", "row": slot["row"], "name": slot["name"], "month": m,
                              "amount": slot.get("revenue", old.get("revenue")), "cost": slot.get("cost"),
                              "old": old.get("revenue"), "old_cost": old.get("cost")})


def _parse_figures(rows, data, today, plan, err) -> None:
    low = [h.lower() for h in rows[0]]
    find = lambda *keys: next((i for i, h in enumerate(low) if any(k in h for k in keys)), None)
    c_p, c_d, c_r = find("product"), find("date"), find("revenue")
    c_c, c_f = find("cost"), find("forecast")
    if c_d is None or c_r is None:
        err(1, None, "The figures template needs Date and Revenue columns.")
        return
    prods = {p["name"].strip().lower(): p for p in data["products"]}
    only = [p for p in data["products"] if p["tracking"] == "figures"]
    new_prods: Dict[str, Dict[str, Any]] = {}
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for p in only:
        for f in p["figures"]:
            merged[(p["name"].lower(), f["day"])] = {"revenue": f["revenue"], "row": None}
    seen: Dict[Tuple[str, str], int] = {}
    for r_i, row in enumerate(rows[1:], start=2):
        cell = lambda c: row[c].strip() if c is not None and c < len(row) else ""
        pname = cell(c_p) or (only[0]["name"] if len(only) == 1 else "")
        if not pname:
            err(r_i, "Product", "Product is empty.")
            continue
        p = prods.get(pname.lower())
        if p and p["tracking"] == "fixed":
            err(r_i, "Product", f"“{pname}” has a fixed monthly amount and takes no figures.")
            continue
        day = parse_day(cell(c_d))
        if not day:
            err(r_i, "Date", f"“{cell(c_d)}” is not a date. Use 16/10/2026 or 2026-10-16." if cell(c_d) else "Date is empty.")
            continue
        if day > today.isoformat():
            err(r_i, "Date", f"{day} is in the future.")
            continue
        if day[:7] < data["settings"]["start_month"]:
            err(r_i, "Date", f"{day} is before the books start.")
            continue
        rev, pr = parse_amount(cell(c_r))
        if pr or rev is None:
            err(r_i, "Revenue", f"Revenue {pr}." if pr else "Revenue is empty.")
            continue
        cost, pc = parse_amount(cell(c_c)) if c_c is not None else (None, None)
        fc, pf = parse_amount(cell(c_f)) if c_f is not None else (None, None)
        if pc or pf:
            err(r_i, "Cost" if pc else "Forecast", f"The amount {pc or pf}.")
            continue
        key = (pname.lower(), day)
        if key in seen:
            err(r_i, "Date", f"{pname} already has a figure for {day} on row {seen[key]}.")
            continue
        seen[key] = r_i
        if not p and pname.lower() not in new_prods:
            new_prods[pname.lower()] = {"row": r_i, "name": pname, "cost_label": "Costs" if cost is not None else None, "start_month": day[:7]}
        if not p and day[:7] < new_prods[pname.lower()]["start_month"]:
            new_prods[pname.lower()]["start_month"] = day[:7]
        old = next((f for f in (p["figures"] if p else []) if f["day"] == day), None)
        merged[key] = {"revenue": rev, "row": r_i}
        plan["cells"].append({"target": "figure", "row": r_i, "name": p["name"] if p else pname, "day": day, "month": day[:7],
                              "amount": rev, "cost": cost, "forecast": fc, "old": old["revenue"] if old else None})
    # revenue so far can only go up within a month, file and existing figures together
    by_month: Dict[Tuple[str, str], List[Tuple[str, Dict[str, Any]]]] = {}
    for (pn, d), v in merged.items():
        by_month.setdefault((pn, d[:7]), []).append((d, v))
    for (pn, m), items in by_month.items():
        items.sort()
        for (d0, a), (d1, b) in zip(items, items[1:]):
            if b["revenue"] < a["revenue"] and (a["row"] or b["row"]):
                err(b["row"] or a["row"], "Revenue", f"{d1}: revenue so far is lower than on {d0}. It can only go up within a month.")
    plan["products"] = list(new_prods.values())


def summarize(plan: Dict[str, Any]) -> Dict[str, Any]:
    cells = plan["cells"]
    def changed(c):
        if c["old"] is None:
            return False
        if abs(float(c["old"]) - float(c["amount"])) > .005:
            return True
        return c.get("cost") is not None and c.get("old_cost") is not None and abs(float(c["old_cost"]) - float(c["cost"])) > .005
    new = [c for c in cells if c["old"] is None]
    upd = [c for c in cells if changed(c)]
    months = sorted({c["month"] for c in new + upd})   # the span of what actually changes
    return {
        "new_categories": len(plan["categories"]), "new_expenses": len(plan["expenses"]), "new_products": len(plan["products"]),
        "new_values": len(new), "changed_values": len(upd), "unchanged_values": len(cells) - len(new) - len(upd),
        "by_target": {t: sum(1 for c in new + upd if c["target"] == t) for t in ("expense", "product_month", "capital", "figure")},
        "first_month": months[0] if months else None, "last_month": months[-1] if months else None,
        "changes": [{"row": c["row"], "name": c["name"], "month": c["month"], "day": c.get("day"), "old": c["old"], "new": c["amount"], "target": c["target"]} for c in upd][:50],
    }
