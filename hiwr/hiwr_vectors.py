"""Money as signed vectors: gross income, total expenses and net, from the HiWR databases.

The idea in one line: every money movement becomes one number with a sign, + for money in and - for money out.
Put those numbers side by side (a vector) and the plain sum IS the net. Group the same vector by day, week,
month, class, department or job and every total comes from that one list, so the totals can't disagree.

    gross income   = money in before anything is taken out (Revenue)
    total expenses = money out that runs the business (COGS, labor, occupancy, operating), shown as a positive
    net            = gross income - refunds - total expenses   (= the sum of the signed vector)

"income + expenses = gross" only works with that sign rule: adding the expenses as negative numbers gives the
NET, not the gross. Gross is the top line before expenses.

What counts, and what stays to the side (agent v7 rules):
  books         confirmed acct_txns rows only (hiwr_accounting). Excluded rows are skipped; rows still waiting
                on a person are listed as "not counted yet". Stock-count changes are added exactly the way
                hiwr_accounting.numbers() adds them, so net here equals its operating profit to the cent.
  off the P&L   capital purchases, owner draws and loan principal: shown, never in net.
  anticipated   open tickets' financial blocks and money actions still PENDING double approval. Kept in their
                own vector and never added to net. Money already posted from a ticket is taken off that ticket's
                exposure, so nothing is counted twice.
  operations    the same vectors built from Stew (dish sales x menu price and plate cost), Warehouse/retail
                (units x price and cost) and Lab (contract vs crew time and sub draws). These are estimates
                from the floor. They are shown beside the books with both values and their sources, and HiWR
                does not pick a winner: a manager decides.
  pulse         this month's net against the average of the 3 months before it, per viewer: dollars for
                Finance and the owner, percentages for managers, one word for everyone else.

Read-only. Nothing here posts, approves or moves money. Money is whole cents (int) everywhere; anything that
starts as a float (hours x rate, recipe quantities) is rounded to the cent with Decimal, half up, once.

Vector math runs on PyTorch or NumPy when installed (int64, exact) and on plain Python otherwise. Pick one
with HIWR_VECTORS=python|numpy|torch; the default uses the best one available. All three give the same cents.
"""
import json
import os
from collections import namedtuple
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

import hiwr_accounting as acct
from hiwr_access import level, read
from hiwr_contacts import money_action_status

# ---------- sign rule ----------
SIGN = {"Revenue": +1, "Contra revenue": -1, "COGS": -1, "Labor": -1, "Occupancy": -1, "Operating": -1}
EXPENSE_GROUPS = ("COGS", "Labor", "Occupancy", "Operating")
OFF_PL = ("Capital", "Balance sheet")
COLUMNS = ("income", "refunds", "expenses")       # the three signed columns; net = their sum
PULSE_LEVELS = ((10, "Great"), (3, "Good"), (-3, "Normal"), (-10, "Slow"))   # % vs 3-month average, else Rough

Entry = namedtuple("Entry", "day cents group cls dept source ref")


def to_cents(x):
    """Any number (float, str, Decimal) to whole cents, half up. Used once, where a float enters."""
    return int(Decimal(str(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def usd(c):
    return f"-${-c / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


# ---------- backends ----------
def backend(name=None):
    """'torch', 'numpy' or 'python'. Asking for one that isn't installed falls back to plain Python."""
    want = (name or os.environ.get("HIWR_VECTORS") or "auto").lower()
    order = ("torch", "numpy", "python") if want == "auto" else (want, "python")
    for b in order:
        if b == "python":
            return b
        try:
            __import__(b)
            return b
        except ImportError:
            continue
    return "python"


def group_sum(rows, idx, n, how=None):
    """rows: one list of signed cents per entry (one value per column). idx: which bucket each entry goes to.
    Returns n buckets, each with the column sums. This is the one place vectors get added up."""
    how = backend(how)
    width = len(rows[0]) if rows else len(COLUMNS)
    if not rows:
        return [[0] * width for _ in range(n)]
    if how == "numpy":
        import numpy as np
        out = np.zeros((n, width), dtype=np.int64)
        np.add.at(out, np.asarray(idx, dtype=np.int64), np.asarray(rows, dtype=np.int64))
        return [[int(v) for v in r] for r in out]
    if how == "torch":
        import torch
        out = torch.zeros((n, width), dtype=torch.int64)
        out.index_add_(0, torch.tensor(idx, dtype=torch.int64), torch.tensor(rows, dtype=torch.int64))
        return out.tolist()
    out = [[0] * width for _ in range(n)]
    for i, r in zip(idx, rows):
        for j, v in enumerate(r):
            out[i][j] += v
    return out


# ---------- bucket keys ----------
def _week(day):
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()      # Monday of that week


AXES = {
    "day": lambda e: e.day,
    "week": lambda e: _week(e.day),
    "month": lambda e: e.day[:7],
    "class": lambda e: e.cls,
    "group": lambda e: e.group,
    "department": lambda e: e.dept or "shared",
    "source": lambda e: e.source,
    "all": lambda e: "total",
}


def _row(e):
    """One entry as its signed columns: [income, refunds, expenses]. Exactly one is non-zero."""
    signed = SIGN[e.group] * e.cents
    if e.group == "Revenue":
        return [signed, 0, 0]
    if e.group == "Contra revenue":
        return [0, signed, 0]
    return [0, 0, signed]


class Vectors:
    """A signed money vector plus the labels of each entry. Built once, grouped any way."""

    def __init__(self, entries, how=None):
        self.entries = list(entries)
        self.how = backend(how)
        self.rows = [_row(e) for e in self.entries]

    @property
    def signed(self):
        return [sum(r) for r in self.rows]

    def by(self, axis="all"):
        key = AXES[axis] if isinstance(axis, str) else axis
        labels = sorted({key(e) for e in self.entries})
        pos = {k: i for i, k in enumerate(labels)}
        sums = group_sum(self.rows, [pos[key(e)] for e in self.entries], len(labels), self.how)
        return {k: _totals(s) for k, s in zip(labels, sums)}

    def total(self):
        return self.by("all").get("total", _totals([0, 0, 0]))


def _totals(s):
    income, refunds, expenses = s
    return {"gross_income": income, "refunds": -refunds, "net_sales": income + refunds,
            "expenses": -expenses, "net": income + refunds + expenses}


# ---------- books (acct_txns) ----------
def books(db, start, end, how=None):
    """Confirmed ledger rows as a signed vector, plus what is kept to the side and why."""
    entries, off_pl, waiting = [], [], []
    for t, e in acct._rows(db, start, end):
        if e["status"] == "excluded":
            continue
        if e["status"] != "confirmed":
            waiting.append({"id": t["id"], "day": t["txn_date"], "kind": t["kind"], "cents": t["amount_cents"],
                            "hiwr_says": e["class"], "status": e["status"]})
            continue
        group = acct.CLASSES[e["class"]][0]
        row = Entry(t["txn_date"], t["amount_cents"], group, e["class"], e["department"], t["source"], t["source_ref"])
        (off_pl if group in OFF_PL else entries).append(row)
    # stock change, the same way numbers() does it (opening count - closing count, on the last day)
    adj = acct.numbers(db, start, end)["inventory_adjustment"]
    for cls, cents in sorted(adj.items()):
        entries.append(Entry(end, cents, "COGS", cls, acct.CLASSES[cls][1], "stock_count",
                             "opening minus closing count"))
    return {"period": (start, end), "vectors": Vectors(entries, how), "off_pl": off_pl, "waiting": waiting}


# ---------- anticipated (tickets + pending money actions) ----------
def _ticket_cents(fin):
    for k in ("amount_cents", "variance_cents", "variance"):
        v = fin.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return to_cents(v)
    return 0


def _posted(db, money_action_id):
    return db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM acct_txns WHERE source='money_action' "
                      "AND source_ref=?", (str(money_action_id),)).fetchone()[0]


def anticipated(db):
    """Open exposure that is NOT in the books yet. Its own vector; never added to net."""
    rows = []
    for t in db.execute("SELECT * FROM tickets WHERE status!='closed' AND financial IS NOT NULL ORDER BY id"):
        fin = json.loads(t["financial"])
        cents = _ticket_cents(fin)
        if not cents:
            continue
        acts = db.execute("SELECT id FROM money_actions WHERE ticket_id=?", (t["id"],)).fetchall()
        booked = sum(_posted(db, a["id"]) for a in acts)
        left = max(0, abs(cents) - booked) * (1 if cents > 0 else -1)
        rows.append({"ref": t["id"], "type": t["type"], "status": t["status"], "lands_in": fin.get("lands_in", "anticipated costs"),
                     "cents": left, "already_booked": booked, "subject": t["subject"]})
    for a in db.execute("SELECT * FROM money_actions WHERE ticket_id IS NULL ORDER BY id"):
        st = money_action_status(db, a["id"])
        if st.startswith("PENDING") or (st == "APPROVED" and not _posted(db, a["id"])):
            sign = +1 if a["scope"] == "fee" else -1
            rows.append({"ref": f"money action {a['id']}", "type": a["scope"].upper(), "status": st,
                         "lands_in": "revenue" if sign > 0 else "expense", "cents": sign * a["amount_cents"],
                         "already_booked": 0, "subject": a["description"]})
    return rows


def anticipated_totals(rows, how=None):
    labels = sorted({r["lands_in"] for r in rows})
    pos = {k: i for i, k in enumerate(labels)}
    sums = group_sum([[r["cents"]] for r in rows], [pos[r["lands_in"]] for r in rows], len(labels), how)
    return {k: s[0] for k, s in zip(labels, sums)}


# ---------- operations (Stew, Warehouse/retail, Lab) ----------
def _plate_cost(db, dish):
    import hiwr_stew
    total = Decimal(0)
    for m, (q, _u, _via) in hiwr_stew.explode(db, dish).items():
        total += Decimal(str(q)) * hiwr_stew.unit_cost(db, m)
    return to_cents(total)


def stew(db, start, end, how=None):
    """Dish sales x menu price = food sales; x plate cost (sub-recipes included) = food cost. Estimates."""
    cost = {}
    entries = []
    for r in db.execute("SELECT s.day, s.dish, SUM(s.qty) qty, d.price_cents FROM dish_sales s JOIN recipes d "
                        "ON d.name=s.dish WHERE s.day BETWEEN ? AND ? GROUP BY s.day, s.dish ORDER BY s.day, s.dish",
                        (start, end)):
        if r["dish"] not in cost:
            cost[r["dish"]] = _plate_cost(db, r["dish"])
        entries.append(Entry(r["day"], r["qty"] * (r["price_cents"] or 0), "Revenue", "food_sales", "food", "stew", r["dish"]))
        entries.append(Entry(r["day"], r["qty"] * cost[r["dish"]], "COGS", "food_cost", "food", "stew", r["dish"]))
    return Vectors(entries, how)


def retail(db, start, end, how=None):
    """Warehouse/retail units sold x shelf price and x unit cost, by product category. Estimates."""
    entries = []
    for r in db.execute("SELECT s.day, s.units, p.sku, p.category, p.price_cents, p.cost_cents FROM daily_sales s "
                        "JOIN products p ON p.sku=s.sku WHERE s.day BETWEEN ? AND ? ORDER BY s.day, p.sku", (start, end)):
        entries.append(Entry(r["day"], r["units"] * r["price_cents"], "Revenue", "retail_sales", r["category"], "retail", r["sku"]))
        entries.append(Entry(r["day"], r["units"] * r["cost_cents"], "COGS", "retail_cost", r["category"], "retail", r["sku"]))
    return Vectors(entries, how)


def lab(db, project_id, how=None):
    """One job: contract value vs crew time (by job role) and sub draws. Retainage held is not paid yet."""
    p = db.execute("SELECT * FROM lab_projects WHERE id=?", (project_id,)).fetchone()
    entries = [Entry(p["starts_on"], p["contract_cents"], "Revenue", "contract", "job", "lab_project", str(p["id"]))]
    for r in db.execute("SELECT t.day, t.hours, t.rate_cents, t.member, ph.name phase, COALESCE(s.role,'unassigned') role "
                        "FROM lab_timesheets t JOIN lab_phases ph ON ph.id=t.phase_id LEFT JOIN staff s ON s.member=t.member "
                        "WHERE ph.project_id=? ORDER BY t.day, t.id", (project_id,)):
        entries.append(Entry(r["day"], to_cents(Decimal(str(r["hours"])) * r["rate_cents"]), "Labor", "crew_" + r["role"],
                             r["phase"], "lab_timesheet", r["member"]))
    held = 0
    for r in db.execute("SELECT d.*, ph.name phase FROM lab_draws d JOIN lab_subcontracts s ON s.id=d.subcontract_id "
                        "JOIN lab_phases ph ON ph.id=s.phase_id WHERE s.project_id=? AND d.status!='blocked' ORDER BY d.id",
                        (project_id,)):
        entries.append(Entry(r["at"][:10], r["gross_cents"] - r["backcharge_cents"], "COGS", "subcontract", r["phase"],
                             "lab_draw", str(r["id"])))
        held += r["retainage_cents"]
    return {"project": p["name"], "vectors": Vectors(entries, how), "retainage_held": held}


def side_by_side(book_v, ops_v, label):
    """Books vs an operations estimate, class by class. Both values and both sources; no winner."""
    b, o = book_v.by("class"), ops_v.by("class")
    out = []
    for c in sorted(set(o)):
        bv = b.get(c, {}).get("gross_income", 0) or b.get(c, {}).get("expenses", 0)
        ov = o[c]["gross_income"] or o[c]["expenses"]
        out.append({"class": c, "books": bv, "operations": ov, "gap": bv - ov,
                    "sources": f"books = confirmed acct_txns; operations = {label}",
                    "decision": "a manager decides which is right" if bv != ov else "match"})
    return out


# ---------- pulse: this month vs the 3 months before ----------
def _month_bounds(ym):
    y, m = map(int, ym.split("-"))
    first = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def _prev(ym, k):
    y, m = map(int, ym.split("-"))
    m -= k
    while m < 1:
        m, y = m + 12, y - 1
    return f"{y:04d}-{m:02d}"


def net_vs_3mo(db, month, how=None, through=None):
    """Net for `month` vs the average net of the 3 months before it. With `through` (month to date), each
    earlier month is cut at the same day, so a partial month is never judged against full ones."""
    start, end = _month_bounds(month)
    if through:
        end = min(end, through)
    now = books(db, start, end, how)
    prior = []
    day_no = int(end[8:])
    for k in (1, 2, 3):
        s, e = _month_bounds(_prev(month, k))
        if end != _month_bounds(month)[1]:          # partial month: compare the same days of each earlier month
            e = min(e, s[:8] + f"{day_no:02d}")
        v = books(db, s, e, how)["vectors"]
        if v.entries:
            prior.append(v.total()["net"])
    t = now["vectors"].total()
    avg = to_cents(Decimal(sum(prior)) / len(prior)) if prior else None
    change = None if avg in (None, 0) else float((Decimal(t["net"] - avg) * 100 / abs(avg)).quantize(Decimal("0.1"), ROUND_HALF_UP))
    word = "No baseline yet" if change is None else next((w for lim, w in PULSE_LEVELS if change >= lim), "Rough")
    margin = None if not t["net_sales"] else float((Decimal(t["net"]) * 100 / t["net_sales"]).quantize(Decimal("0.1"), ROUND_HALF_UP))
    return {"month": month, "through": end, "partial": end != _month_bounds(month)[1], **t,
            "margin_pct": margin, "avg_3mo_net": avg, "months_in_avg": len(prior), "change_pct": change, "pulse": word,
            "not_counted_yet": len(now["waiting"])}


def pulse(db, member, month, through=None, how=None):
    """One line per viewer. Real numbers only: no rounding up, no hiding a bad month."""
    p = net_vs_3mo(db, month, how, through)
    w = p["not_counted_yet"]
    tail = f" ({w} entr{'y' if w == 1 else 'ies'} still waiting on a person)" if w else ""
    part = f" through {p['through']}" if p["partial"] else ""
    base = (f"vs 3-month average {usd(p['avg_3mo_net'])} ({p['change_pct']:+.1f}%)" if p["change_pct"] is not None
            else f"no 3-month baseline yet ({p['months_in_avg']} month(s) of history)")
    if p["partial"] and p["change_pct"] is not None:
        base += f", same days 1-{int(p['through'][8:])} of each month"

    def full():
        return (f"{p['pulse']}: {month}{part} gross income {usd(p['gross_income'])}, expenses {usd(p['expenses'])}, "
                f"net {usd(p['net'])} {base}{tail}")

    def reduced():
        if level(db, member) >= 2:
            ch = f"{p['change_pct']:+.1f}% vs 3-month average" if p["change_pct"] is not None else "no baseline yet"
            return f"{p['pulse']}: {month}{part} net margin {p['margin_pct']}% of net sales, {ch}{tail}"
        return f"{p['pulse']}: {month}{part}"

    return read(db, member, "financial_block", full, reduced)


# ---------- text report (Finance+) ----------
def report(db, member, start, end, axis="month", how=None):
    if level(db, member) < 3:
        raise PermissionError(f"the income/expense vectors show dollars; needs Finance level (3); "
                              f"{member} is level {level(db, member)}")
    b = books(db, start, end, how)
    v = b["vectors"]
    L = [f"Income and expenses {start} to {end}, by {axis} (confirmed entries only, {v.how} vectors)"]
    L.append(f"   {'':12} {'gross income':>14} {'refunds':>11} {'expenses':>14} {'net':>14}")
    for k, t in v.by(axis).items():
        L.append(f"   {k:12} {usd(t['gross_income']):>14} {usd(-t['refunds']):>11} {usd(-t['expenses']):>14} {usd(t['net']):>14}")
    t = v.total()
    L.append(f"   {'total':12} {usd(t['gross_income']):>14} {usd(-t['refunds']):>11} {usd(-t['expenses']):>14} {usd(t['net']):>14}")
    L.append(f"   check: sum of the signed vector = {usd(sum(v.signed))} (same as net)")
    if b["off_pl"]:
        L.append(f"   Not in net (capital / balance sheet): {usd(sum(e.cents for e in b['off_pl']))}")
    if b["waiting"]:
        L.append(f"   Not counted yet: {len(b['waiting'])} entr{'y' if len(b['waiting']) == 1 else 'ies'}, {usd(sum(w['cents'] for w in b['waiting']))}, waiting on a person")
    ant = anticipated(db)
    if ant:
        L.append("   Anticipated, NOT in net (open tickets and money waiting on double approval):")
        for r in ant:
            extra = f", {usd(r['already_booked'])} already in the books" if r["already_booked"] else ""
            L.append(f"      {r['ref']:16} {r['status']:28} {usd(r['cents']):>11}  -> {r['lands_in']}{extra}")
        for k, c in anticipated_totals(ant, how).items():
            L.append(f"      total {k:24} {usd(c):>11}")
    return "\n".join(L)
