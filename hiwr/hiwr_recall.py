"""Recall: the recurring numbers and averages behind the business, pulled straight from the data (no model).

Each fact is a short card: a title, the number, a plain-language line, and tags to search on (item, person,
vendor, client, line). Facts are scoped by level: staff see only facts about themselves; costs and money need
Manager or Finance level. Standard library only; every source is optional, so a new account just has fewer facts.
"""
import sqlite3
from collections import defaultdict
from datetime import date

WEEKDAYS = ["Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays"]


def _rows(db, sql, args=()):
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


def _usd(c):
    return f"${c / 100:,.2f}"


def facts(db, member, level, today, name_of=lambda m: m, prior_invoices=()):
    out = []

    def add(title, value, text, tags, kind, who=None):
        out.append({"title": title, "value": value, "text": text, "tags": [t.lower() for t in tags], "kind": kind, "who": who})

    # ---- sales: typical day per dish, by weekday, from the last 4 weeks (Lead and up) ----
    if level >= 1:
        wd = date.fromisoformat(today).weekday()
        per = defaultdict(lambda: defaultdict(int))
        hours = defaultdict(lambda: defaultdict(int))
        for r in _rows(db, "SELECT day, hour, dish, qty FROM dish_sales WHERE day < ?", (today,)):
            per[r["dish"]][r["day"]] += r["qty"]
            hours[r["dish"]][r["hour"]] += r["qty"]
        for dish, days in sorted(per.items()):
            same = [q for d, q in days.items() if date.fromisoformat(d).weekday() == wd]
            if not same:
                continue
            avg = sum(same) / len(same)
            peak = max(hours[dish].items(), key=lambda x: x[1])[0]
            add(f"{dish}: a typical {WEEKDAYS[wd][:-1]}", f"{avg:.0f} sold",
                f"Average of the last {len(same)} {WEEKDAYS[wd]}. Busiest hour: {peak}:00.", [dish, "sales", "average"], "sales")

    # ---- cooks: average time and remakes per person per item (Mesh history) ----
    try:
        from hiwr_mesh import mesh
        m = mesh(db, member=member if level < 1 else None)
    except (ImportError, sqlite3.OperationalError):
        m = {"by_member": {}}
    for who, items in m["by_member"].items():
        for x in items[:3]:
            add(f"{name_of(who)} on {x['item']}", f"{x['avg_min']} min" if x["avg_min"] is not None else f"{x['made']} made",
                f"{x['made']} made in the last two weeks, {x['remake_pct']}% remade.", [name_of(who), x["item"], "cook", "time"],
                "people", who)

    # ---- vendors: how often deliveries come up short, and what was really paid (Manager and up) ----
    if level >= 2 and prior_invoices:
        by_vendor = defaultdict(list)
        for x in prior_invoices:
            by_vendor[x["vendor"]].append(x)
        for v, xs in by_vendor.items():
            short = [x for x in xs if x["received_qty"] < x["billed_qty"]]
            add(f"{v}: deliveries short", f"{len(short)} of {len(xs)}",
                f"{len(short)} of the last {len(xs)} invoices arrived short. Pay only for what arrived; shorts go on a ticket.",
                [v, "vendor", "short", "invoice"], "vendors")
            mats = defaultdict(lambda: [0, 0])
            for x in xs:
                if x["status"] in ("paid", "paid_short"):
                    mats[x["material"]][0] += x["paid_cents"]
                    mats[x["material"]][1] += x["received_qty"]
            for mat, (paid, qty) in mats.items():
                if qty:
                    add(f"{mat}: what you really pay", f"{paid / qty:.0f}¢ each", f"From paid {v} invoices, per unit received.",
                        [mat, v, "cost", "price"], "vendors")

    # ---- materials: average daily use from sales moves (Manager and up) ----
    if level >= 2:
        use = defaultdict(lambda: defaultdict(float))
        for r in _rows(db, "SELECT material, qty, substr(at,1,10) AS d FROM material_moves WHERE qty < 0"):
            use[r["material"]][r["d"]] -= r["qty"]
        for mat, days in sorted(use.items()):
            if days:
                avg = sum(days.values()) / len(days)
                on = _rows(db, "SELECT on_hand, unit FROM materials WHERE material=?", (mat,))
                left = f" About {on[0]['on_hand'] / avg:.1f} days of it on hand." if on and avg else ""
                add(f"{mat}: daily use", f"{avg:g} {on[0]['unit'] if on else ''}/day".strip(), f"Average on days it was used.{left}",
                    [mat, "inventory", "use", "reorder"], "inventory")

    # ---- portfolio lines and clients ----
    for ln in _rows(db, "SELECT * FROM pf_lines"):
        runs = _rows(db, "SELECT * FROM pf_runs WHERE line=?", (ln["id"],))
        if not runs or level < 1:
            continue
        units = sum(r["units"] for r in runs)
        days = len({r["started_at"][:10] for r in runs})
        add(f"{ln['name']}: a typical day", f"{units / days:.1f} {ln['unit']}", f"Average over the last {days} days.",
            [ln["name"], ln["unit"], "average", "line"], "lines")
        if level >= 2:
            add(f"{ln['name']}: average job", _usd(sum(r["revenue_cents"] for r in runs) / len(runs)),
                f"Average revenue per run or visit, {len(runs)} in all.", [ln["name"], "average", "ticket"], "lines")
    if level >= 2:
        clients = defaultdict(list)
        for r in _rows(db, "SELECT client, revenue_cents, started_at FROM pf_runs WHERE client IS NOT NULL"):
            clients[r["client"]].append(r)
        for c, rs in sorted(clients.items(), key=lambda x: -len(x[1]))[:6]:
            add(f"{c}: regular client", f"{len(rs)} jobs", f"Average job {_usd(sum(r['revenue_cents'] for r in rs) / len(rs))}; last on {max(r['started_at'] for r in rs)[:10]}.",
                [c, "client", "recurring"], "clients")
        for a in _rows(db, "SELECT a.client, a.due_on FROM pf_appointments a WHERE a.due_on < ?", (today,)):
            paid = _rows(db, "SELECT COALESCE(SUM(CASE WHEN kind IN ('invoice','late_fee') THEN amount_cents ELSE -amount_cents END),0) AS b "
                             "FROM pf_ledger l JOIN pf_appointments p ON p.id=l.appt_id WHERE p.client=?", (a["client"],))
            if paid and paid[0]["b"] > 0:
                add(f"{a['client']}: pays late", _usd(paid[0]["b"]), f"Balance still open after {a['due_on']}.",
                    [a["client"], "late", "client", "payment"], "clients")

    # ---- money: averages (Finance and up) ----
    if level >= 3:
        for scope in ("refund", "fee", "payroll"):
            rs = _rows(db, "SELECT amount_cents FROM money_actions WHERE scope=?", (scope,))
            if rs:
                add(f"Average {scope} request", _usd(sum(r["amount_cents"] for r in rs) / len(rs)),
                    f"{len(rs)} {scope} request(s) so far; each needs two approvers.", [scope, "money", "average"], "money")

    # ---- staff below Lead: only facts about themselves ----
    if level < 1:
        out = [f for f in out if f["who"] == member]
    return out


def search(all_facts, q):
    words = [w for w in (q or "").lower().split() if w]
    if not words:
        return all_facts
    def hit(f):
        hay = " ".join([f["title"].lower(), f["text"].lower(), *f["tags"]])
        return all(w in hay for w in words)
    return [f for f in all_facts if hit(f)]
