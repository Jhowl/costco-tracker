from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from statistics import median
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from . import matcher, returns


def _person(person: Optional[int], alias: str = "r") -> Tuple[str, list]:
    return (f" AND {alias}.person_id = ?", [person]) if person else ("", [])


def _rows(cur) -> List[Dict]:
    return [dict(r) for r in cur.fetchall()]


def people(con: sqlite3.Connection) -> List[Dict]:
    return _rows(con.execute(
        """SELECT p.id, p.name, p.color, COUNT(r.id) AS receipts, MIN(r.purchase_date) AS first,
                  MAX(r.purchase_date) AS last
           FROM people p LEFT JOIN receipts r ON r.person_id = p.id GROUP BY p.id ORDER BY p.id"""))


# ---------------------------------------------------------------- dashboard

def summary(con: sqlite3.Connection, person: Optional[int]) -> Dict:
    pf, pp = _person(person)
    totals = dict(con.execute(
        f"""SELECT COUNT(*) AS receipts, COALESCE(SUM(total),0) AS spent,
                   COALESCE(SUM(instant_savings),0) AS saved, MIN(purchase_date) AS first,
                   MAX(purchase_date) AS last,
                   COUNT(DISTINCT purchase_date) AS visit_days
            FROM receipts r WHERE 1=1{pf}""", pp).fetchone())
    by_month = _rows(con.execute(
        f"""SELECT substr(purchase_date,1,7) AS month, person_id, COUNT(*) AS receipts,
                   ROUND(SUM(total),2) AS spent
            FROM receipts r WHERE 1=1{pf} GROUP BY 1,2 ORDER BY 1""", pp))
    by_person = _rows(con.execute(
        f"""SELECT person_id, COUNT(*) AS receipts, ROUND(SUM(total),2) AS spent
            FROM receipts r WHERE 1=1{pf} GROUP BY 1""", pp))
    by_category = _rows(con.execute(
        f"""SELECT COALESCE(p.category,'Not categorised') AS category,
                   ROUND(SUM(pu.amount + pu.discount),2) AS spent, COUNT(*) AS lines
            FROM purchases pu JOIN products p ON p.id = pu.product_id JOIN receipts r ON r.id = pu.receipt_id
            WHERE 1=1{pf} GROUP BY 1 ORDER BY 2 DESC""", pp))
    top = products(con, person, sort="times")[:15]
    return {"totals": totals, "by_month": by_month, "by_person": by_person,
            "by_category": by_category, "top_products": top}


# ---------------------------------------------------------------- home page

def _last_year(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:  # Feb 29
        return d.replace(year=d.year - 1, day=28)


def _period(con: sqlite3.Connection, start: date, end: date, pf: str, pp: list) -> Dict:
    d = dict(con.execute(
        f"""SELECT COUNT(*) AS receipts, COALESCE(SUM(total),0) AS spent,
                   COALESCE(SUM(instant_savings),0) AS saved, COUNT(DISTINCT purchase_date) AS trips
            FROM receipts r WHERE purchase_date BETWEEN ? AND ?{pf}""",
        [start.isoformat(), end.isoformat(), *pp]).fetchone())
    d["spent"], d["saved"] = round(d["spent"], 2), round(d["saved"], 2)
    d["avg_trip"] = round(d["spent"] / d["trips"], 2) if d["trips"] else None
    d["start"], d["end"] = start.isoformat(), end.isoformat()
    return d


def home(con: sqlite3.Connection, person: Optional[int], today: Optional[date] = None) -> Dict:
    today = today or date.today()
    pf, pp = _person(person)
    y = today.year
    ly_today = _last_year(today)
    month_start = today.replace(day=1)
    prev_end = month_start - timedelta(days=1)
    prev_start = prev_end.replace(day=1)

    periods = {
        "ytd": _period(con, date(y, 1, 1), today, pf, pp),
        "last_ytd": _period(con, date(y - 1, 1, 1), ly_today, pf, pp),
        "last_year": _period(con, date(y - 1, 1, 1), date(y - 1, 12, 31), pf, pp),
        "mtd": _period(con, month_start, today, pf, pp),
        "last_year_mtd": _period(con, ly_today.replace(day=1), ly_today, pf, pp),
        "prev_mtd": _period(con, prev_start, prev_start.replace(day=min(today.day, prev_end.day)), pf, pp),
        "prev_month": _period(con, prev_start, prev_end, pf, pp),
    }
    days_in_year = (date(y + 1, 1, 1) - date(y, 1, 1)).days
    day_of_year = today.timetuple().tm_yday
    ytd, last_ytd = periods["ytd"], periods["last_ytd"]
    # pace: last year's full total scaled by how this year compares so far (keeps seasonality);
    # without last year's data, a straight-line projection
    if last_ytd["spent"] > 0 and periods["last_year"]["spent"] > 0:
        projection = periods["last_year"]["spent"] * ytd["spent"] / last_ytd["spent"]
    else:
        projection = ytd["spent"] / day_of_year * days_in_year
    span = dict(con.execute(f"SELECT MIN(purchase_date) AS first, MAX(purchase_date) AS last FROM receipts r WHERE 1=1{pf}", pp).fetchone())

    monthly = {m: {"month": m, "this_year": 0.0, "last_year": 0.0, "this_trips": 0, "last_trips": 0} for m in range(1, 13)}
    for row in con.execute(
            f"""SELECT substr(purchase_date,1,4) AS yr, CAST(substr(purchase_date,6,2) AS INTEGER) AS m,
                       SUM(total) AS spent, COUNT(DISTINCT purchase_date) AS trips
                FROM receipts r WHERE substr(purchase_date,1,4) IN (?, ?){pf} GROUP BY 1, 2""",
            [str(y), str(y - 1), *pp]):
        key = "this" if row["yr"] == str(y) else "last"
        monthly[row["m"]][f"{key}_year"] = round(row["spent"], 2)
        monthly[row["m"]][f"{key}_trips"] = row["trips"]

    def by_person(start: date, end: date) -> Dict[int, Dict]:
        return {r["person_id"]: r for r in _rows(con.execute(
            f"""SELECT person_id, COUNT(*) AS receipts, ROUND(SUM(total),2) AS spent,
                       COUNT(DISTINCT purchase_date) AS trips
                FROM receipts r WHERE purchase_date BETWEEN ? AND ?{pf} GROUP BY 1""",
            [start.isoformat(), end.isoformat(), *pp]))}
    pt, pl = by_person(date(y, 1, 1), today), by_person(date(y - 1, 1, 1), ly_today)
    people_split = [{"person_id": k, "spent": pt.get(k, {}).get("spent", 0), "trips": pt.get(k, {}).get("trips", 0),
                     "last_spent": pl.get(k, {}).get("spent", 0)} for k in sorted(set(pt) | set(pl))]

    def categories(start: date, end: date) -> Dict[str, float]:
        return {r["category"]: r["spent"] for r in _rows(con.execute(
            f"""SELECT COALESCE(p.category,'Not categorised') AS category, ROUND(SUM(pu.amount + pu.discount),2) AS spent
                FROM purchases pu JOIN products p ON p.id = pu.product_id JOIN receipts r ON r.id = pu.receipt_id
                WHERE r.purchase_date BETWEEN ? AND ?{pf} GROUP BY 1""",
            [start.isoformat(), end.isoformat(), *pp]))}
    ct, cl = categories(date(y, 1, 1), today), categories(date(y - 1, 1, 1), ly_today)
    cats = sorted(({"category": c, "spent": ct.get(c, 0), "last_spent": cl.get(c, 0)} for c in set(ct) | set(cl)),
                  key=lambda c: -c["spent"])

    gas = dict(con.execute(
        f"""SELECT COALESCE(SUM(pu.quantity),0) AS gallons, COALESCE(SUM(pu.amount + pu.discount),0) AS spent,
                   COUNT(DISTINCT r.id) AS fills
            FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id
            WHERE r.kind = 'gas' AND pu.quantity > 0 AND r.purchase_date BETWEEN ? AND ?{pf}""",
        [f"{y}-01-01", today.isoformat(), *pp]).fetchone())
    gas["avg_price"] = round(gas["spent"] / gas["gallons"], 3) if gas["gallons"] else None
    gas["gallons"], gas["spent"] = round(gas["gallons"], 1), round(gas["spent"], 2)

    rows = _rows(con.execute(
        f"""SELECT pu.product_id, COALESCE(p.group_id, p.id) AS group_id, p.name, p.receipt_name, p.category,
                   pu.quantity, pu.unit_price, ROUND(pu.amount + pu.discount, 2) AS paid, pu.discount,
                   r.purchase_date, r.id AS receipt_id, r.person_id
            FROM purchases pu JOIN products p ON p.id = pu.product_id JOIN receipts r ON r.id = pu.receipt_id
            WHERE pu.quantity > 0 AND r.kind != 'gas'{pf} ORDER BY r.purchased_at""", pp))

    # staples that are probably running low: bought on 3+ days, and it's been about as long as usual
    groups: Dict[int, List[Dict]] = defaultdict(list)
    for r in rows:
        groups[r["group_id"]].append(r)
    restock = []
    for g in groups.values():
        days = sorted({date.fromisoformat(r["purchase_date"]) for r in g})
        if len(days) < 3:
            continue
        every = median([(b - a).days for a, b in zip(days, days[1:])])
        since = (today - days[-1]).days
        if every <= 0 or since > 365:
            continue
        ratio = since / every
        if 0.9 <= ratio <= 3:
            last = g[-1]
            restock.append({"product_id": last["product_id"], "name": last["name"], "receipt_name": last["receipt_name"],
                            "every_days": round(every), "days_since": since, "times": len(days),
                            "last_price": last["unit_price"], "overdue_days": round(since - every), "ratio": round(ratio, 2)})
    restock.sort(key=lambda r: (-min(r["ratio"], 1.5), -r["times"]))

    changes = _price_changes(rows, (today - timedelta(days=120)).isoformat())

    this_year = [r for r in rows if r["purchase_date"] >= f"{y}-01-01"]
    top: Dict[int, Dict] = {}
    for r in this_year:
        t = top.setdefault(r["group_id"], {"product_id": r["product_id"], "name": r["name"],
                                           "receipt_name": r["receipt_name"], "trips": set(), "spent": 0.0})
        t["trips"].add(r["purchase_date"])
        t["spent"] += r["paid"]
        t["product_id"], t["name"], t["receipt_name"] = r["product_id"], r["name"], r["receipt_name"]
    top_list = sorted(({**t, "trips": len(t["trips"]), "spent": round(t["spent"], 2)} for t in top.values()),
                      key=lambda t: (-t["trips"], -t["spent"]))
    biggest = sorted(this_year, key=lambda r: -r["paid"])[:6]

    years = _rows(con.execute(
        f"""SELECT substr(purchase_date,1,4) AS year, COUNT(*) AS receipts, ROUND(SUM(total),2) AS spent,
                   COUNT(DISTINCT purchase_date) AS trips, ROUND(SUM(instant_savings),2) AS saved,
                   MIN(purchase_date) AS first, MAX(purchase_date) AS last
            FROM receipts r WHERE 1=1{pf} GROUP BY 1 ORDER BY 1""", pp))
    for yr in years:
        yr["avg_trip"] = round(yr["spent"] / yr["trips"], 2) if yr["trips"] else None
        yr["partial"] = yr["year"] == str(y) or (yr["first"] or "")[5:] > "01-31"
    all_time = _period(con, date.fromisoformat(span["first"]) if span["first"] else today, today, pf, pp)
    months_span = max(1, (today.year - int(span["first"][:4])) * 12 + today.month - int(span["first"][5:7]) + 1) if span["first"] else 1
    all_time["per_month"] = round(all_time["spent"] / months_span, 2)
    s_all = summary(con, person)

    rv = returns_view(con, person, today)
    return {
        "all_time": all_time, "years": years, "all_time_monthly": s_all["by_month"],
        "all_time_categories": s_all["by_category"][:12], "all_time_top": s_all["top_products"][:10],
        "today": today.isoformat(), "year": y, "periods": periods,
        "projection": round(projection, 2), "day_of_year": day_of_year, "days_in_year": days_in_year,
        "first_date": span["first"], "last_date": span["last"],
        "days_since_visit": (today - date.fromisoformat(span["last"])).days if span["last"] else None,
        "monthly": list(monthly.values()), "people": people_split, "categories": cats, "gas": gas,
        "restock": restock[:8], "price_changes": changes[:8],
        "top_this_year": top_list[:10], "biggest_this_year": biggest,
        "returns_open": rv["open"][:5], "returns_open_count": len(rv["open"]),
        "recent_receipts": receipts(con, person, limit=6),
    }


# ---------------------------------------------------------------- shared helpers

def _line_rows(con: sqlite3.Connection, pf: str, pp: list, where: str = "", params: tuple = ()) -> List[Dict]:
    """Every purchase line with its product and receipt, oldest first."""
    return _rows(con.execute(
        f"""SELECT pu.product_id, COALESCE(p.group_id, p.id) AS group_id, p.name, p.receipt_name,
                   COALESCE(p.category, 'Not categorised') AS category, pu.quantity, pu.unit_price,
                   ROUND(pu.amount + pu.discount, 2) AS paid, pu.discount, r.purchase_date, r.purchased_at,
                   r.id AS receipt_id, r.person_id, r.kind, r.warehouse
            FROM purchases pu JOIN products p ON p.id = pu.product_id JOIN receipts r ON r.id = pu.receipt_id
            WHERE 1=1{pf}{where} ORDER BY r.purchased_at""", [*pp, *params]))


def _price_changes(rows: List[Dict], since: str) -> List[Dict]:
    """Shelf-price change between the last two purchases of each product, when the last one is >= since."""
    by_product: Dict[int, List[Dict]] = defaultdict(list)
    for r in rows:
        if r["quantity"] > 0 and r.get("kind") != "gas":
            by_product[r["product_id"]].append(r)
    changes = []
    for ps in by_product.values():
        last = ps[-1]
        prev = next((p for p in reversed(ps) if p["purchase_date"] < last["purchase_date"]), None)
        if not prev or last["purchase_date"] < since or not prev["unit_price"] or not last["unit_price"]:
            continue
        diff = last["unit_price"] - prev["unit_price"]
        if abs(diff) >= 0.01 and abs(diff) / prev["unit_price"] >= 0.01:
            changes.append({"product_id": last["product_id"], "name": last["name"], "receipt_name": last["receipt_name"],
                            "old": prev["unit_price"], "new": last["unit_price"], "old_date": prev["purchase_date"],
                            "date": last["purchase_date"], "pct": round(100 * diff / prev["unit_price"], 1)})
    changes.sort(key=lambda c: (c["date"], abs(c["pct"])), reverse=True)
    return changes


def _top_products(rows: List[Dict], limit: int = 8) -> Dict[str, List[Dict]]:
    groups: Dict[int, Dict] = {}
    for r in rows:
        if r["quantity"] <= 0:
            continue
        g = groups.setdefault(r["group_id"], {"trips": set(), "spent": 0.0, "units": 0.0})
        g["trips"].add(r["purchase_date"])
        g["spent"] += r["paid"]
        g["units"] += r["quantity"]
        g.update(product_id=r["product_id"], name=r["name"], receipt_name=r["receipt_name"], category=r["category"])
    out = [{**g, "trips": len(g["trips"]), "spent": round(g["spent"], 2), "units": round(g["units"], 2)}
           for g in groups.values()]
    return {"by_trips": sorted(out, key=lambda g: (-g["trips"], -g["spent"]))[:limit],
            "by_spent": sorted(out, key=lambda g: -g["spent"])[:limit]}


def _category_totals(rows: List[Dict]) -> Dict[str, float]:
    out: Dict[str, float] = defaultdict(float)
    for r in rows:
        out[r["category"]] += r["paid"]
    return {k: round(v, 2) for k, v in out.items()}


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------- any period (calendar month / year / all time)

def period_summary(con: sqlite3.Connection, start: str, end: str, person: Optional[int],
                   vs_start: Optional[str] = None, vs_end: Optional[str] = None) -> Dict:
    pf, pp = _person(person)
    totals = _period(con, date.fromisoformat(start), date.fromisoformat(end), pf, pp)
    upto = _line_rows(con, pf, pp, " AND r.purchase_date <= ?", (end,))
    lines = [r for r in upto if r["purchase_date"] >= start]
    bought = [r for r in lines if r["quantity"] > 0]

    cats = _category_totals(lines)
    vs, vs_cats = None, {}
    if vs_start and vs_end:
        vs = _period(con, date.fromisoformat(vs_start), date.fromisoformat(vs_end), pf, pp)
        vs_cats = _category_totals(_line_rows(con, pf, pp, " AND r.purchase_date BETWEEN ? AND ?", (vs_start, vs_end)))
    spent_items = sum(v for v in cats.values() if v > 0) or 1
    categories = sorted(({"category": c, "spent": v, "share": round(100 * v / spent_items, 1),
                          "vs_spent": vs_cats.get(c)} for c, v in cats.items() if v > 0), key=lambda c: -c["spent"])

    first_seen: Dict[int, str] = {}
    for r in upto:
        if r["quantity"] > 0:
            first_seen.setdefault(r["group_id"], r["purchase_date"])
    new = {}
    for r in bought:
        if first_seen.get(r["group_id"], "") >= start and r["kind"] != "gas":
            new.setdefault(r["group_id"], {"product_id": r["product_id"], "name": r["name"],
                                           "receipt_name": r["receipt_name"], "date": r["purchase_date"],
                                           "paid": r["paid"], "category": r["category"]})

    days = {r["purchase_date"] for r in bought}
    weekdays = [0] * 7
    for d in days:
        weekdays[date.fromisoformat(d).weekday()] += 1
    warehouses: Dict[str, int] = defaultdict(int)
    for rid, wh in {(r["receipt_id"], (r["warehouse"] or "?").title()) for r in lines if r["kind"] == "warehouse"}:
        warehouses[wh] += 1
    gas = [r for r in bought if r["kind"] == "gas"]
    refunds = _rows(con.execute(
        f"""SELECT COUNT(*) AS n, COALESCE(SUM(total),0) AS total FROM receipts r
            WHERE is_refund = 1 AND purchase_date BETWEEN ? AND ?{pf}""", [start, end, *pp]))[0]
    biggest = _rows(con.execute(
        f"""SELECT r.id, r.purchase_date, r.person_id, r.warehouse, r.total,
                   (SELECT COUNT(*) FROM purchases WHERE receipt_id = r.id) AS lines
            FROM receipts r WHERE purchase_date BETWEEN ? AND ? AND is_refund = 0{pf}
            ORDER BY total DESC LIMIT 3""", [start, end, *pp]))
    people = _rows(con.execute(
        f"""SELECT person_id, ROUND(SUM(total),2) AS spent, COUNT(DISTINCT purchase_date) AS trips
            FROM receipts r WHERE purchase_date BETWEEN ? AND ?{pf} GROUP BY 1 ORDER BY 2 DESC""", [start, end, *pp]))
    return {
        "start": start, "end": end, "totals": totals, "vs": vs, "vs_start": vs_start, "vs_end": vs_end,
        "lines": len(bought), "units": round(sum(r["quantity"] for r in bought if r["kind"] != "gas"), 1),
        "distinct_products": len({r["group_id"] for r in bought}),
        "categories": categories, "top": _top_products(bought),
        "new_products": sorted(new.values(), key=lambda n: -n["paid"])[:8], "new_count": len(new),
        "price_changes": [c for c in _price_changes(upto, start)][:8],
        "weekdays": [{"day": WEEKDAYS[i], "trips": n} for i, n in enumerate(weekdays)],
        "warehouses": sorted(warehouses.items(), key=lambda kv: -kv[1]),
        "gas": {"gallons": round(sum(r["quantity"] for r in gas), 1), "spent": round(sum(r["paid"] for r in gas), 2),
                "fills": len({r["receipt_id"] for r in gas})},
        "refunds": {"count": refunds["n"], "total": round(refunds["total"], 2)},
        "biggest_receipts": biggest, "people": people,
    }


# ---------------------------------------------------------------- categories

def categories_overview(con: sqlite3.Connection, person: Optional[int], today: Optional[date] = None) -> Dict:
    today = today or date.today()
    pf, pp = _person(person)
    y, ly_today = today.year, _last_year(today).isoformat()
    rows = _line_rows(con, pf, pp)
    cats: Dict[str, Dict] = {}
    year_totals: Dict[str, float] = defaultdict(float)
    for r in rows:
        c = cats.setdefault(r["category"], {"category": r["category"], "spent": 0.0, "ytd": 0.0, "last_ytd": 0.0,
                                            "by_year": defaultdict(float), "products": set(), "trips": set(),
                                            "units": 0.0, "last": None, "top": defaultdict(float), "names": {}})
        yr = r["purchase_date"][:4]
        c["spent"] += r["paid"]
        c["by_year"][yr] += r["paid"]
        year_totals[yr] += r["paid"]
        if yr == str(y):
            c["ytd"] += r["paid"]
        elif yr == str(y - 1) and r["purchase_date"] <= ly_today:
            c["last_ytd"] += r["paid"]
        if r["quantity"] > 0:
            c["products"].add(r["group_id"])
            c["trips"].add(r["purchase_date"])
            c["units"] += r["quantity"]
            c["last"] = r["purchase_date"]
            c["top"][r["group_id"]] += r["paid"]
            c["names"][r["group_id"]] = (r["product_id"], r["name"] or r["receipt_name"])
    total = sum(c["spent"] for c in cats.values()) or 1
    out = []
    for c in cats.values():
        top_gid = max(c["top"], key=c["top"].get) if c["top"] else None
        out.append({
            "category": c["category"], "spent": round(c["spent"], 2), "share": round(100 * c["spent"] / total, 1),
            "ytd": round(c["ytd"], 2), "last_ytd": round(c["last_ytd"], 2),
            "by_year": {k: round(v, 2) for k, v in sorted(c["by_year"].items())},
            "products": len(c["products"]), "trips": len(c["trips"]), "units": round(c["units"], 1),
            "per_trip": round(c["spent"] / len(c["trips"]), 2) if c["trips"] else None, "last": c["last"],
            "top_product": {"id": c["names"][top_gid][0], "name": c["names"][top_gid][1],
                            "spent": round(c["top"][top_gid], 2)} if top_gid is not None else None,
        })
    out.sort(key=lambda c: -c["spent"])
    return {"categories": out, "years": sorted(year_totals), "year_totals": {k: round(v, 2) for k, v in year_totals.items()},
            "total": round(total, 2), "year": y, "today": today.isoformat()}


def category_detail(con: sqlite3.Connection, name: str, person: Optional[int], today: Optional[date] = None) -> Dict:
    today = today or date.today()
    pf, pp = _person(person)
    rows = [r for r in _line_rows(con, pf, pp) if r["category"] == name]
    all_rows = _line_rows(con, pf, pp)
    bought = [r for r in rows if r["quantity"] > 0]
    y, ly_today = today.year, _last_year(today).isoformat()
    monthly: Dict[str, float] = defaultdict(float)
    by_year: Dict[str, Dict] = {}
    for r in rows:
        monthly[r["purchase_date"][:7]] += r["paid"]
        b = by_year.setdefault(r["purchase_date"][:4], {"spent": 0.0, "trips": set(), "units": 0.0})
        b["spent"] += r["paid"]
        if r["quantity"] > 0:
            b["trips"].add(r["purchase_date"])
            b["units"] += r["quantity"]
    year_totals = _category_totals([dict(r, category=r["purchase_date"][:4]) for r in all_rows])
    first = min((r["purchase_date"] for r in all_rows), default=today.isoformat())
    months, cur = [], date.fromisoformat(first[:7] + "-01")
    while cur <= today:
        key = cur.strftime("%Y-%m")
        months.append({"month": key, "spent": round(monthly.get(key, 0.0), 2)})
        cur = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
    people: Dict[int, Dict] = {}
    for r in bought:
        p = people.setdefault(r["person_id"], {"person_id": r["person_id"], "spent": 0.0, "trips": set()})
        p["spent"] += r["paid"]
        p["trips"].add(r["purchase_date"])
    ytd = sum(r["paid"] for r in rows if r["purchase_date"][:4] == str(y))
    last_ytd = sum(r["paid"] for r in rows if r["purchase_date"][:4] == str(y - 1) and r["purchase_date"] <= ly_today)
    trips = {r["purchase_date"] for r in bought}
    return {
        "category": name, "year": y, "today": today.isoformat(),
        "spent": round(sum(r["paid"] for r in rows), 2), "ytd": round(ytd, 2), "last_ytd": round(last_ytd, 2),
        "trips": len(trips), "units": round(sum(r["quantity"] for r in bought), 1),
        "per_trip": round(sum(r["paid"] for r in bought) / len(trips), 2) if trips else None,
        "first": bought[0]["purchase_date"] if bought else None, "last": bought[-1]["purchase_date"] if bought else None,
        "per_month": round(sum(r["paid"] for r in rows) / max(1, len(months)), 2),
        "monthly": months,
        "by_year": [{"year": k, "spent": round(v["spent"], 2), "trips": len(v["trips"]), "units": round(v["units"], 1),
                     "share": round(100 * v["spent"] / year_totals[k], 1) if year_totals.get(k) else None}
                    for k, v in sorted(by_year.items())],
        "products": products(con, person, category=name, sort="spent"),
        "price_changes": _price_changes(rows, (today - timedelta(days=365)).isoformat())[:10],
        "people": [{**p, "spent": round(p["spent"], 2), "trips": len(p["trips"])} for p in people.values()],
    }


# ---------------------------------------------------------------- calendar

def _group_ids(con: sqlite3.Connection, product_id: int) -> List[int]:
    row = con.execute("SELECT group_id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        return []
    gid = row["group_id"] or product_id
    return [r[0] for r in con.execute("SELECT id FROM products WHERE group_id = ? OR id = ?", (gid, product_id))]


def calendar(con: sqlite3.Connection, start: str, end: str, person: Optional[int],
             product_id: Optional[int]) -> List[Dict]:
    pf, pp = _person(person)
    if product_id:
        ids = _group_ids(con, product_id)
        marks = ",".join("?" * len(ids))
        return _rows(con.execute(
            f"""SELECT r.purchase_date AS date, r.person_id, COUNT(DISTINCT r.id) AS receipts,
                       ROUND(SUM(pu.amount + pu.discount),2) AS total, SUM(pu.quantity) AS items
                FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id
                WHERE pu.product_id IN ({marks}) AND r.purchase_date BETWEEN ? AND ?{pf}
                GROUP BY 1,2 ORDER BY 1""", [*ids, start, end, *pp]))
    return _rows(con.execute(
        f"""SELECT r.purchase_date AS date, r.person_id, COUNT(*) AS receipts, ROUND(SUM(r.total),2) AS total,
                   (SELECT COUNT(*) FROM purchases pu JOIN receipts r2 ON r2.id = pu.receipt_id
                     WHERE r2.purchase_date = r.purchase_date AND r2.person_id = r.person_id) AS items
            FROM receipts r WHERE r.purchase_date BETWEEN ? AND ?{pf}
            GROUP BY 1,2 ORDER BY 1""", [start, end, *pp]))


# ---------------------------------------------------------------- receipts

def receipts(con: sqlite3.Connection, person: Optional[int], start: Optional[str] = None,
             end: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[Dict]:
    pf, pp = _person(person)
    where, params = "1=1" + pf, list(pp)
    if start:
        where += " AND r.purchase_date >= ?"
        params.append(start)
    if end:
        where += " AND r.purchase_date <= ?"
        params.append(end)
    return _rows(con.execute(
        f"""SELECT r.id, r.barcode, r.person_id, r.purchased_at, r.purchase_date, r.warehouse, r.kind,
                   r.is_refund, r.subtotal, r.taxes, r.total, r.instant_savings, r.source,
                   (SELECT COUNT(*) FROM purchases WHERE receipt_id = r.id) AS lines
            FROM receipts r WHERE {where} ORDER BY r.purchased_at DESC LIMIT ? OFFSET ?""",
        [*params, limit, offset]))


def receipt(con: sqlite3.Connection, rid: int) -> Optional[Dict]:
    row = con.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    out.pop("raw", None)
    out["items"] = _rows(con.execute(
        """SELECT pu.*, p.item_number, p.receipt_name, p.name, p.category, p.return_class
           FROM purchases pu JOIN products p ON p.id = pu.product_id
           WHERE pu.receipt_id = ? ORDER BY pu.line_no""", (rid,)))
    for it in out["items"]:
        it["return"] = returns.status(it["return_class"], out["purchase_date"], returned=it["quantity"] < 0)
    return out


# ---------------------------------------------------------------- products

def products(con: sqlite3.Connection, person: Optional[int], q: Optional[str] = None,
             category: Optional[str] = None, sort: str = "last") -> List[Dict]:
    pf, pp = _person(person)
    rows = _rows(con.execute(
        f"""SELECT p.id, p.item_number, p.receipt_name, p.name, p.brand, p.category, p.return_class,
                   p.ai_status, p.group_id,
                   COUNT(DISTINCT CASE WHEN pu.quantity > 0 THEN r.id END) AS times,
                   SUM(CASE WHEN pu.quantity > 0 THEN pu.quantity ELSE 0 END) AS quantity,
                   SUM(CASE WHEN pu.quantity < 0 THEN 1 ELSE 0 END) AS returns,
                   ROUND(SUM(pu.amount + pu.discount), 2) AS spent,
                   MIN(CASE WHEN pu.quantity > 0 THEN pu.unit_price END) AS min_price,
                   MAX(CASE WHEN pu.quantity > 0 THEN pu.unit_price END) AS max_price,
                   MIN(r.purchase_date) AS first, MAX(r.purchase_date) AS last,
                   GROUP_CONCAT(DISTINCT r.person_id) AS buyers
            FROM products p JOIN purchases pu ON pu.product_id = p.id JOIN receipts r ON r.id = pu.receipt_id
            WHERE 1=1{pf} GROUP BY p.id""", pp))
    last_prices = {r["product_id"]: r for r in _rows(con.execute(
        f"""SELECT pu.product_id, pu.unit_price AS last_price,
                   ROUND((pu.amount + pu.discount) / pu.quantity, 2) AS last_paid,
                   MAX(r.purchased_at) AS at
            FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id
            WHERE pu.quantity > 0{pf} GROUP BY pu.product_id""", pp))}
    for p in rows:
        lp = last_prices.get(p["id"], {})
        p["last_price"], p["last_paid"] = lp.get("last_price"), lp.get("last_paid")
        p["buyers"] = [int(x) for x in (p["buyers"] or "").split(",") if x]
        p["return"] = returns.status(p["return_class"], p["last"])
    if category:
        rows = [p for p in rows if (p["category"] or "Not categorised") == category]
    if q:
        return [dict(p, score=round(s)) for p, s in matcher.search(rows, q)]
    key = {"times": lambda p: (-p["times"], p["last"] or ""), "spent": lambda p: -(p["spent"] or 0),
           "name": lambda p: (p["name"] or p["receipt_name"]).lower()}.get(sort)
    if key:
        return sorted(rows, key=key)
    return sorted(rows, key=lambda p: p["last"] or "", reverse=True)


def _mark_returned(purchases: List[Dict]) -> None:
    """Pair each refund line with the latest earlier purchase by the same person."""
    for refund in sorted((p for p in purchases if p["quantity"] < 0), key=lambda p: p["purchased_at"]):
        for p in sorted(purchases, key=lambda p: p["purchased_at"], reverse=True):
            if (p["quantity"] > 0 and not p.get("returned") and p["person_id"] == refund["person_id"]
                    and p["product_id"] == refund["product_id"] and p["purchased_at"] <= refund["purchased_at"]):
                p["returned"] = True
                break


def product(con: sqlite3.Connection, pid: int, person: Optional[int],
            today: Optional[date] = None) -> Optional[Dict]:
    row = con.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if not row:
        return None
    out = dict(row)
    pf, pp = _person(person)
    purchases = _rows(con.execute(
        f"""SELECT pu.id, pu.product_id, pu.quantity, pu.unit_price, pu.amount, pu.discount,
                   r.id AS receipt_id, r.purchased_at, r.purchase_date, r.person_id, r.warehouse
            FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id
            WHERE pu.product_id = ?{pf} ORDER BY r.purchased_at""", [pid, *pp]))
    _mark_returned(purchases)
    for p in purchases:
        p["paid"] = round(p["amount"] + p["discount"], 2)
        p["paid_unit"] = round(p["paid"] / p["quantity"], 2) if p["quantity"] else None
        p["return"] = returns.status(out["return_class"], p["purchase_date"], returned=bool(p.get("returned")))
    out["purchases"] = purchases
    bought = [p for p in purchases if p["quantity"] > 0]
    out["times"] = len({p["receipt_id"] for p in bought})
    out["policy"] = returns.POLICY.get(out["return_class"] or "", None)
    gaps = [(date.fromisoformat(b["purchase_date"]) - date.fromisoformat(a["purchase_date"])).days
            for a, b in zip(bought, bought[1:])]
    gaps = [g for g in gaps if g > 0]
    # typical gap (median): one long break doesn't stretch it
    out["avg_days_between"] = round(median(gaps)) if gaps else None
    out["siblings"] = _rows(con.execute(
        "SELECT id, item_number, receipt_name, name FROM products WHERE group_id = ? AND id != ?",
        (out["group_id"] or pid, pid)))
    out["stats"] = _product_stats(bought, purchases, out["avg_days_between"], today or date.today())
    out["receipt_details"] = _receipt_details(con, out["item_number"], bought)
    return out


def _product_stats(bought: List[Dict], purchases: List[Dict], every: Optional[int], today: date) -> Dict:
    if not bought:
        return {}
    prices = [p["unit_price"] for p in bought if p["unit_price"]]
    first, last = bought[0], bought[-1]
    low = min(bought, key=lambda p: p["paid_unit"] if p["paid_unit"] is not None else 1e9)
    last_d = date.fromisoformat(last["purchase_date"])
    by_year: Dict[str, Dict] = {}
    for p in bought:
        y = by_year.setdefault(p["purchase_date"][:4], {"times": set(), "quantity": 0.0, "spent": 0.0})
        y["times"].add(p["receipt_id"])
        y["quantity"] += p["quantity"]
        y["spent"] += p["paid"]
    by_person: Dict[int, Dict] = {}
    for p in bought:
        b = by_person.setdefault(p["person_id"], {"person_id": p["person_id"], "times": set(), "spent": 0.0})
        b["times"].add(p["receipt_id"])
        b["spent"] += p["paid"]
    warehouses: Dict[str, int] = {}
    for p in bought:
        if p["warehouse"]:
            warehouses[p["warehouse"].title()] = warehouses.get(p["warehouse"].title(), 0) + 1
    next_due = (last_d + timedelta(days=every)).isoformat() if every else None
    return {
        "first_date": first["purchase_date"], "last_date": last["purchase_date"],
        "days_since_last": (today - last_d).days,
        "next_expected": next_due,
        "due_in_days": (date.fromisoformat(next_due) - today).days if next_due else None,
        "total_quantity": round(sum(p["quantity"] for p in bought), 3),
        "avg_quantity_per_trip": round(sum(p["quantity"] for p in bought) / len({p["receipt_id"] for p in bought}), 2),
        "total_spent": round(sum(p["paid"] for p in purchases), 2),
        "total_saved": round(-sum(p["discount"] for p in bought), 2),
        "times_on_sale": sum(1 for p in bought if p["discount"] < 0),
        "first_price": first["unit_price"], "last_price": last["unit_price"],
        "min_price": min(prices) if prices else None, "max_price": max(prices) if prices else None,
        "avg_price": round(sum(prices) / len(prices), 2) if prices else None,
        "price_change_pct": round(100 * (last["unit_price"] / first["unit_price"] - 1), 1)
        if first["unit_price"] and len(bought) > 1 else None,
        "best_paid_unit": low["paid_unit"], "best_paid_date": low["purchase_date"],
        "returns": sum(1 for p in purchases if p["quantity"] < 0),
        "by_year": [{"year": y, "times": len(v["times"]), "quantity": round(v["quantity"], 3),
                     "spent": round(v["spent"], 2)} for y, v in sorted(by_year.items())],
        "by_person": [{"person_id": k, "times": len(v["times"]), "spent": round(v["spent"], 2)}
                      for k, v in by_person.items()],
        "warehouses": sorted(warehouses.items(), key=lambda kv: -kv[1]),
    }


def _receipt_details(con: sqlite3.Connection, item_number: str, bought: List[Dict]) -> Dict:
    """Extra fields costco.com prints for this item (2nd description line, department, tax)."""
    if not bought:
        return {}
    raw = con.execute("SELECT raw FROM receipts WHERE id = ?", (bought[-1]["receipt_id"],)).fetchone()
    try:
        items = json.loads(raw["raw"]).get("itemArray") or []
    except (TypeError, ValueError, AttributeError):
        return {}
    for it in items:
        if str(it.get("itemNumber") or "") == item_number:
            return {"description2": (it.get("itemDescription02") or "").strip() or None,
                    "department": it.get("itemDepartmentNumber"),
                    "taxable": {"Y": True, "N": False}.get(it.get("taxFlag"))}
    return {}


def update_product(con: sqlite3.Connection, pid: int, fields: Dict) -> None:
    allowed = {k: v for k, v in fields.items() if k in {"name", "brand", "category", "return_class", "return_note"}}
    if not allowed:
        return
    if "return_class" in allowed or "category" in allowed:
        cur = dict(con.execute("SELECT category, return_class FROM products WHERE id = ?", (pid,)).fetchone())
        cur.update(allowed)
        allowed["return_class"] = returns.apply_rules(cur["category"], cur["return_class"])
    sets = ", ".join(f"{k} = ?" for k in allowed)
    con.execute(f"UPDATE products SET {sets}, ai_status = 'manual' WHERE id = ?", [*allowed.values(), pid])


# ---------------------------------------------------------------- returns

def returns_view(con: sqlite3.Connection, person: Optional[int], today: Optional[date] = None) -> Dict:
    today = today or date.today()
    pf, pp = _person(person)
    since = (today - timedelta(days=150)).isoformat()
    rows = _rows(con.execute(
        f"""SELECT pu.id, pu.product_id, pu.quantity, pu.unit_price, pu.amount, pu.discount,
                   r.id AS receipt_id, r.purchased_at, r.purchase_date, r.person_id, r.warehouse,
                   p.name, p.receipt_name, p.item_number, p.category, p.return_class, p.return_note
            FROM purchases pu JOIN receipts r ON r.id = pu.receipt_id JOIN products p ON p.id = pu.product_id
            WHERE (p.return_class IN ('electronics_90','limited') OR p.return_class IS NULL)
              AND (p.return_class = 'limited' OR r.purchase_date >= ?){pf}
            ORDER BY r.purchased_at DESC""", [since, *pp]))
    _mark_returned(rows)
    out = {"open": [], "expired": [], "limited": [], "uncategorised": 0, "policy": returns.POLICY}
    for r in rows:
        if r["quantity"] < 0 or r.get("returned"):
            continue
        r["paid"] = round(r["amount"] + r["discount"], 2)
        r["return"] = returns.status(r["return_class"], r["purchase_date"], today)
        state = r["return"]["state"]
        if state == "open":
            out["open"].append(r)
        elif state == "expired" and r["return"]["days_left"] >= -60:
            out["expired"].append(r)
        elif state == "check":
            out["limited"].append(r)
        elif state == "unknown":
            out["uncategorised"] += 1
    out["open"].sort(key=lambda r: r["return"]["days_left"])
    return out


# ---------------------------------------------------------------- AI context

STOPWORDS = set("""a an the i we my our me us you did do does have has how many much when what which who where
why is was were are be been can could should would will to of in on for from at by with and or not it this
that these those times time ever bought buy buying purchase purchased last first costco wife husband price
paid pay spend spent return returnable still""".split())


def ask_context(con: sqlite3.Connection, question: str, person: Optional[int]) -> Dict:
    names = {p["id"]: p["name"] for p in people(con)}
    words = [w for w in re.findall(r"[a-zA-Z0-9]+", question.lower()) if w not in STOPWORDS]
    allp = products(con, person)
    # search each keyword and the whole phrase; products in the same fuzzy group are one product
    groups: Dict[int, List[int]] = {}
    for term in words + ([" ".join(words)] if len(words) > 1 else []):
        for p, score in matcher.search(allp, term, limit=6):
            if score >= 85:
                ids = groups.setdefault(p["group_id"] or p["id"], [])
                if p["id"] not in ids:
                    ids.append(p["id"])
    relevant = []
    for ids in list(groups.values())[:10]:
        details = [product(con, pid, person) for pid in ids]
        d = details[0]
        purchases = sorted((x for det in details for x in det["purchases"]), key=lambda x: x["purchased_at"])
        relevant.append({
            "product": d["name"] or d["receipt_name"],
            "receipt_texts": sorted({det["receipt_name"] for det in details}),
            "item_numbers": [det["item_number"] for det in details],
            "category": d["category"], "return_class": d["return_class"],
            "times_bought": len({x["receipt_id"] for x in purchases if x["quantity"] > 0}),
            "total_quantity": sum(x["quantity"] for x in purchases if x["quantity"] > 0),
            "purchases": [{"date": x["purchase_date"], "who": names.get(x["person_id"]), "qty": x["quantity"],
                           "unit_price": x["unit_price"], "paid": x["paid"], "returned": bool(x.get("returned")),
                           "return_status": x["return"]["label"]} for x in purchases[-60:]],
        })
    s = summary(con, person)
    return {
        "today": date.today().isoformat(),
        "people": list(names.values()),
        "filtered_to": names.get(person) if person else "everyone",
        "totals": s["totals"],
        "spend_by_person": [{"who": names.get(r["person_id"]), **r} for r in s["by_person"]],
        "spend_by_month": [{"who": names.get(r["person_id"]), **r} for r in s["by_month"][-36:]],
        "spend_by_category": s["by_category"],
        "most_bought": [{"product": p["name"] or p["receipt_name"], "times": p["times"], "spent": p["spent"],
                         "last": p["last"], "last_price": p["last_price"]} for p in s["top_products"]],
        "matching_products": relevant,
    }
