"""Pioneering phase: the first setup of a business in HiWR, before Human has anything to learn from.

Three ways in, use any or all:
  establish_orders           - enter what you sell (item, UPC/PLU, price, department) and ring a first order through HiWR
  establish_routine_sales    - enter each day's totals by hand (from the register's Z-report) until there are 2 full weeks
  connect_transaction_system - map a POS export (Square, Toast, Clover...) once, preview it, then import it
The phase is done when HiWR knows what is sold (orders or a connection) and has 14 days of sales, which become the
first baseline sample. Until then Human only records; it doesn't suggest.
"""
import json
from datetime import date, timedelta
from hiwr_access import level
from hiwr_contacts import _is_owner
from hiwr_accounting import post

PATHS = ("establish_orders", "establish_routine_sales", "connect_transaction_system")
MIN_ITEMS = 5
MIN_DAYS = 14


def _step(db, path, step, by, detail=None):
    db.execute("INSERT INTO pioneer_steps(path, step, detail, by) VALUES (?,?,?,?)", (path, step, detail, by))


def start(db, owner, paths):
    if not _is_owner(db, owner):
        raise PermissionError("the account holder starts the pioneering phase")
    for p in paths:
        if p not in PATHS:
            raise ValueError(f"unknown path {p}")
        _step(db, p, "started", owner)


# ---- establish orders ----
def add_item(db, member, upc, item, price_cents, department):
    if level(db, member) < 2:
        raise PermissionError("adding what you sell needs Manager level (2)")
    db.execute("INSERT INTO catalog(upc, item, price_cents) VALUES (?,?,?)", (upc, item, price_cents))
    db.execute("INSERT INTO catalog_meta(upc, department) VALUES (?,?)", (upc, department))
    _step(db, "establish_orders", "item_added", member, f"{item} {upc}")


def first_order(db, member, order_ref):
    _step(db, "establish_orders", "first_order", member, order_ref)


# ---- establish routine sales ----
def record_day(db, member, day, by_department, sales_tax_cents=0, source_note="Z-report"):
    """by_department: {"food": cents, "beverage": cents}. Posts through the ledger, so it's deduped and classified."""
    if level(db, member) < 2:
        raise PermissionError("entering daily sales needs Manager level (2)")
    for dept, cents in by_department.items():
        post(db, day, "revenue", cents, f"daily sales ({source_note})", "pos", f"{day}-{dept}", member, department=dept)
    if sales_tax_cents:
        post(db, day, "revenue", sales_tax_cents, "sales tax collected", "pos_tax", f"{day}-tax", member)
    _step(db, "establish_routine_sales", "day_recorded", member, day)


# ---- connect a transaction system ----
def connect(db, owner, system, method, mapping):
    """mapping: {"columns": {date,item,category,qty,net,tax}, "departments": {category: department}}"""
    if not _is_owner(db, owner):
        raise PermissionError("only the account holder connects a transaction system")
    cid = db.execute("INSERT INTO pos_connections(system, method, mapping, status, connected_by) VALUES (?,?,?,?,?)",
                     (system, method, json.dumps(mapping), "mapped", owner)).lastrowid
    _step(db, "connect_transaction_system", "mapped", owner, f"{system} via {method}")
    return cid


def _cents(s):
    return round(float(str(s).replace("$", "").replace(",", "")) * 100)


def _parse(db, cid, rows):
    m = json.loads(db.execute("SELECT mapping FROM pos_connections WHERE id=?", (cid,)).fetchone()[0])
    c, depts = m["columns"], m["departments"]
    days, items, problems = {}, [], []
    for i, r in enumerate(rows, 1):
        cat = r.get(c["category"], "")
        if cat not in depts:
            problems.append(f"row {i}: category '{cat}' has no department; map it before importing")
            continue
        d = days.setdefault(r[c["date"]], {"tax": 0})
        dept = depts[cat]
        d[dept] = d.get(dept, 0) + _cents(r[c["net"]])
        d["tax"] += _cents(r.get(c["tax"], 0) or 0)
        items.append((r[c["date"]], r[c["item"]], int(r[c["qty"]])))
    return days, items, problems


def preview(db, cid, rows):
    """Nothing is written. Shows what would post and what can't be mapped."""
    days, items, problems = _parse(db, cid, rows)
    return {"days": len(days), "lines": len(items), "problems": problems,
            "totals": {d: {k: v for k, v in t.items()} for d, t in sorted(days.items())[:3]}}


def run_import(db, member, cid, rows):
    if level(db, member) < 3 and not _is_owner(db, member):
        raise PermissionError("importing sales into the books needs Finance level (3)")
    days, items, problems = _parse(db, cid, rows)
    if problems:
        raise ValueError(f"{len(problems)} rows can't be mapped; fix the mapping first: {problems[0]}")
    posted = skipped = 0
    for day, t in days.items():
        tax = t.pop("tax")
        for dept, cents in t.items():
            try:
                post(db, day, "revenue", cents, "POS import", "pos", f"{day}-{dept}", member, department=dept)
                posted += 1
            except ValueError:
                skipped += 1           # already in the books (hand entry or earlier import)
        if tax:
            try:
                post(db, day, "revenue", tax, "sales tax collected", "pos_tax", f"{day}-tax", member)
            except ValueError:
                pass
    for day, item, qty in items:
        db.execute("INSERT INTO dish_sales(day, hour, dish, qty) VALUES (?,?,?,?)", (day, 0, item, qty))
    db.execute("UPDATE pos_connections SET status='importing' WHERE id=?", (cid,))
    _step(db, "connect_transaction_system", "imported", member, f"{posted} posted, {skipped} already there")
    return {"posted": posted, "skipped_duplicates": skipped, "item_lines": len(items)}


# ---- status ----
def status(db):
    items = db.execute("SELECT COUNT(*) FROM catalog_meta").fetchone()[0]
    first = db.execute("SELECT 1 FROM pioneer_steps WHERE step='first_order'").fetchone() is not None
    connected = db.execute("SELECT 1 FROM pioneer_steps WHERE step='imported'").fetchone() is not None
    sale_days = sorted(r[0] for r in db.execute("SELECT DISTINCT txn_date FROM acct_txns WHERE source='pos'"))
    full_weeks = 0
    if sale_days:
        s, e = date.fromisoformat(sale_days[0]), date.fromisoformat(sale_days[-1])
        have = set(sale_days)
        d = s + timedelta(days=(7 - s.weekday()) % 7)              # first Monday
        while d + timedelta(days=6) <= e:
            if all((d + timedelta(days=i)).isoformat() in have for i in range(7)):
                full_weeks += 1
            d += timedelta(days=7)
    knows_what_sells = (items >= MIN_ITEMS and first) or connected
    done = knows_what_sells and len(sale_days) >= MIN_DAYS and full_weeks >= 2
    return {"items": items, "first_order": first, "connected": connected, "sale_days": len(sale_days),
            "full_weeks": full_weeks, "knows_what_sells": knows_what_sells, "done": done,
            "next": None if done else
            ("add items and ring a first order, or connect your POS" if not knows_what_sells else
             f"keep recording sales: {len(sale_days)}/{MIN_DAYS} days, {full_weeks}/2 full weeks")}
