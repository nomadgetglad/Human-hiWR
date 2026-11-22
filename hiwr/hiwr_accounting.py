"""Accounting view for HiWR (Finance level and up).

Every money movement is posted once (source + source_ref is unique). HiWR proposes a class for each one,
a Finance-level person confirms or corrects it, and only confirmed rows count in the numbers. Expenses are
then crossed with revenue: food/beverage cost %, labor %, prime cost, occupancy, operating profit, and
contribution by department. QuickBooks stays the source of truth: HiWR compares and flags, it never overwrites.
Managers (level 2) get a reduced view: percentages for the kitchen, no payroll dollars.
"""
from datetime import date, timedelta
from hiwr_access import level, read
from hiwr_contacts import money_action_status

# class -> (group, default department)
CLASSES = {
    "food_sales": ("Revenue", "food"), "beverage_sales": ("Revenue", "beverage"),
    "catering_sales": ("Revenue", "catering"), "late_fees": ("Revenue", "shared"),
    "refunds_comps": ("Contra revenue", None),
    "food_cost": ("COGS", "food"), "beverage_cost": ("COGS", "beverage"), "packaging": ("COGS", "shared"),
    "subcontract": ("COGS", "shared"),
    "wages": ("Labor", None), "payroll_tax": ("Labor", "shared"), "benefits": ("Labor", "shared"),
    "rent": ("Occupancy", "shared"), "utilities": ("Occupancy", "shared"),
    "equipment_repair": ("Operating", "shared"), "card_fees": ("Operating", "shared"),
    "delivery_fees": ("Operating", "shared"), "supplies": ("Operating", "shared"),
    "insurance": ("Operating", "shared"), "marketing": ("Operating", "shared"),
    "equipment_purchase": ("Capital", "shared"),     # not a P&L expense; the accountant decides depreciation
    "owner_draw": ("Balance sheet", None), "loan_principal": ("Balance sheet", None),
}
PL_GROUPS = ("Revenue", "Contra revenue", "COGS", "Labor", "Occupancy", "Operating")

KEYWORDS = [  # (words in counterparty/description, class) - first match wins
    (("sysco", "us foods", "produce", "seafood", "bakery", "meat"), "food_cost"),
    (("beverage", "beer", "wine", "liquor", "coca-cola", "pepsi"), "beverage_cost"),
    (("to-go", "clamshell", "packaging", "webstaurant"), "packaging"),
    (("rent", "lease", "landlord"), "rent"),
    (("duke energy", "electric", "water", "gas co", "piedmont natural", "internet", "spectrum"), "utilities"),
    (("square", "stripe", "merchant", "card processing"), "card_fees"),
    (("doordash", "uber eats", "grubhub"), "delivery_fees"),
    (("repair", "service call", "hvac", "refrigeration"), "equipment_repair"),
    (("insurance",), "insurance"),
    (("facebook ads", "google ads", "flyer", "marketing"), "marketing"),
    (("payroll tax", "irs", "ncdor", "futa", "suta"), "payroll_tax"),
    (("cleaning", "chemicals", "ecolab", "paper"), "supplies"),
]
SOURCE_CLASS = {"payroll": "wages", "timeclock": "wages", "timeclock_tax": "payroll_tax"}
CAPITAL_HINTS = ("new ", "purchase", "replace", "install", "compressor", "oven", "fryer unit")
CAPITAL_MIN_CENTS = 2500_00
DEFAULT_TARGETS = {  # common full-service rules of thumb; the owner should set their own
    "food_cost_pct": (28, 35), "beverage_cost_pct": (18, 24), "labor_pct": (25, 35),
    "prime_cost_pct": (None, 65), "occupancy_pct": (None, 10),
}
MOVE_FLAG_POINTS = 2.0


def _money(c):
    return f"-${-c / 100:,.2f}" if c < 0 else f"${c / 100:,.2f}"


# ---------- posting ----------
def post(db, txn_date, kind, amount_cents, description, source, source_ref, posted_by,
         counterparty=None, department=None):
    """Post one real money movement and propose its class. Re-posting the same source record is refused."""
    if db.execute("SELECT 1 FROM acct_txns WHERE source=? AND source_ref=?", (source, source_ref)).fetchone():
        raise ValueError(f"{source} {source_ref} is already posted (no double counting)")
    tid = db.execute("INSERT INTO acct_txns(txn_date, kind, amount_cents, description, counterparty, department, "
                     "source, source_ref, posted_by) VALUES (?,?,?,?,?,?,?,?,?)",
                     (txn_date, kind, amount_cents, description, counterparty, department,
                      source, source_ref, posted_by)).lastrowid
    propose(db, tid)
    return tid


def post_money_action(db, money_action_id, txn_date, posted_by, department=None):
    """Approved refunds, payroll runs, fees and sub draws flow in from money actions, and only once approved."""
    act = db.execute("SELECT * FROM money_actions WHERE id=?", (money_action_id,)).fetchone()
    status = money_action_status(db, money_action_id)
    if status != "APPROVED":
        raise PermissionError(f"money action {money_action_id} is {status}; it posts after approval")
    kind = "revenue" if act["scope"] == "fee" else "expense"
    return post(db, txn_date, kind, act["amount_cents"], act["description"], "money_action",
                str(money_action_id), posted_by, department=department)


# ---------- classification ----------
def _latest(db, tid):
    return db.execute("SELECT * FROM acct_class_events WHERE txn_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()


def _event(db, tid, cls, dept, status, by, reason):
    db.execute("INSERT INTO acct_class_events(txn_id, class, department, status, by, reason) VALUES (?,?,?,?,?,?)",
               (tid, cls, dept, status, by, reason))


def propose(db, tid):
    t = db.execute("SELECT * FROM acct_txns WHERE id=?", (tid,)).fetchone()
    text = f"{t['counterparty'] or ''} {t['description']}".lower()
    dept = t["department"]
    # 1) possible duplicate of a movement already posted from another source
    dup = db.execute("SELECT id, source, source_ref FROM acct_txns WHERE id<>? AND kind=? AND amount_cents=? "
                     "AND lower(coalesce(counterparty,''))=lower(coalesce(?,'')) AND source<>? "
                     "AND abs(julianday(txn_date)-julianday(?))<=3",
                     (tid, t["kind"], t["amount_cents"], t["counterparty"], t["source"], t["txn_date"])).fetchone()
    if dup:
        return _event(db, tid, "duplicate?", dept, "needs_person", "hiwr",
                      f"same amount and payee as #{dup['id']} ({dup['source']} {dup['source_ref']}) within 3 days; "
                      "if it's the payment of that invoice, exclude it")
    # 2) a rule a person taught
    rule = db.execute("SELECT * FROM acct_rules WHERE lower(counterparty)=lower(?) ORDER BY id DESC LIMIT 1",
                      (t["counterparty"] or "",)).fetchone()
    if rule:
        return _event(db, tid, rule["class"], rule["department"] or dept, "proposed", "hiwr",
                      f"rule from {rule['created_by']}")
    # 3) where it came from
    if t["source"] == "pos":
        cls = {"food": "food_sales", "beverage": "beverage_sales", "catering": "catering_sales"}.get(dept)
        if cls:
            return _event(db, tid, cls, dept, "proposed", "hiwr", "POS sales by department")
    if t["source"] == "money_action":
        scope = db.execute("SELECT scope FROM money_actions WHERE id=?", (int(t["source_ref"]),)).fetchone()["scope"]
        cls = {"refund": "refunds_comps", "payroll": "wages", "fee": "late_fees", "subcontract": "subcontract"}[scope]
        return _event(db, tid, cls, dept or CLASSES[cls][1], "proposed", "hiwr", f"approved {scope} money action")
    if t["source"] in SOURCE_CLASS:
        return _event(db, tid, SOURCE_CLASS[t["source"]], dept, "proposed", "hiwr", f"from {t['source']}")
    # 4) words in the payee/description
    for words, cls in KEYWORDS:
        if any(w in text for w in words):
            if cls in ("equipment_repair", "supplies") and t["amount_cents"] >= CAPITAL_MIN_CENTS \
                    and any(h in text for h in CAPITAL_HINTS):
                return _event(db, tid, "equipment_purchase", "shared", "needs_person", "hiwr",
                              f"{_money(t['amount_cents'])} and reads like a replacement, so it may be capital, "
                              "not a repair. Ask the accountant")
            return _event(db, tid, cls, dept or CLASSES[cls][1], "proposed", "hiwr", f"matched '{next(w for w in words if w in text)}'")
    if t["amount_cents"] >= CAPITAL_MIN_CENTS and any(h in text for h in CAPITAL_HINTS):
        return _event(db, tid, "equipment_purchase", "shared", "needs_person", "hiwr",
                      "large and reads like a purchase; may be capital. Ask the accountant")
    return _event(db, tid, "unclassified", dept, "needs_person", "hiwr", "no rule matches; a person picks the class")


def _need_finance(db, member, what):
    if level(db, member) < 3:
        raise PermissionError(f"{what} needs Finance level (3); {member} is level {level(db, member)}")


def confirm(db, tid, member, cls=None, department=None, learn=True, reason=None):
    """Finance confirms the proposal, or corrects it. A correction teaches a rule for that payee."""
    _need_finance(db, member, "confirming a classification")
    cur = _latest(db, tid)
    t = db.execute("SELECT * FROM acct_txns WHERE id=?", (tid,)).fetchone()
    cls = cls or cur["class"]
    if cls not in CLASSES:
        raise ValueError(f"pick a real class for #{tid} (got {cls})")
    dept = department or cur["department"] or CLASSES[cls][1]
    _event(db, tid, cls, dept, "confirmed", member, reason or ("confirmed proposal" if cls == cur["class"] else
                                                               f"corrected from {cur['class']}"))
    if learn and cls != cur["class"] and t["counterparty"] and cur["class"] not in ("duplicate?",):
        db.execute("INSERT INTO acct_rules(counterparty, class, department, created_by) VALUES (?,?,?,?)",
                   (t["counterparty"], cls, dept if dept != "shared" else None, member))


def exclude(db, tid, member, reason):
    """For a duplicate or a transfer between own accounts. The row stays; it just doesn't count."""
    _need_finance(db, member, "excluding a transaction")
    _event(db, tid, "excluded", None, "excluded", member, reason)


def confirm_all_proposed(db, member, start, end):
    """Batch-confirm the routine ones (POS, payroll, known payees). Anything needing a person is left alone."""
    _need_finance(db, member, "confirming")
    n = 0
    for t in db.execute("SELECT id FROM acct_txns WHERE txn_date BETWEEN ? AND ?", (start, end)).fetchall():
        e = _latest(db, t["id"])
        if e["status"] == "proposed":
            _event(db, t["id"], e["class"], e["department"], "confirmed", member, "batch confirm")
            n += 1
    return n


def review_queue(db, start, end):
    out = []
    for t in db.execute("SELECT * FROM acct_txns WHERE txn_date BETWEEN ? AND ? ORDER BY txn_date, id", (start, end)):
        e = _latest(db, t["id"])
        if e["status"] in ("needs_person", "proposed"):
            out.append({"id": t["id"], "date": t["txn_date"], "payee": t["counterparty"], "what": t["description"],
                        "amount": _money(t["amount_cents"]), "hiwr_says": e["class"], "status": e["status"],
                        "why": e["reason"]})
    return out


# ---------- numbers ----------
def _rows(db, start, end):
    for t in db.execute("SELECT * FROM acct_txns WHERE txn_date BETWEEN ? AND ?", (start, end)).fetchall():
        yield t, _latest(db, t["id"])


def targets(db):
    out = dict(DEFAULT_TARGETS)
    for r in db.execute("SELECT * FROM acct_targets ORDER BY id"):
        out[r["metric"]] = (r["low"], r["high"])
    return out


def set_target(db, member, metric, low, high):
    if level(db, member) < 4:
        raise PermissionError("targets are set by the account holder")
    db.execute("INSERT INTO acct_targets(metric, low, high, set_by) VALUES (?,?,?,?)", (metric, low, high, member))


def _pct(a, b):
    return round(100 * a / b, 1) if b else None


def numbers(db, start, end):
    by_class, by_group, unconfirmed, dept = {}, {g: 0 for g in CLASSES_GROUPS()}, [0, 0], {}
    for t, e in _rows(db, start, end):
        if e["status"] == "excluded":
            continue
        if e["status"] != "confirmed":
            unconfirmed[0] += 1
            unconfirmed[1] += t["amount_cents"]
            continue
        c = e["class"]
        by_class[c] = by_class.get(c, 0) + t["amount_cents"]
        by_group[CLASSES[c][0]] += t["amount_cents"]
        d = e["department"] or "shared"
        g = CLASSES[c][0]
        if g in ("Revenue", "COGS", "Labor", "Contra revenue"):
            slot = dept.setdefault(d, {"Revenue": 0, "COGS": 0, "Labor": 0, "Contra revenue": 0})
            slot[g] += t["amount_cents"]
    # purchases -> cost of goods used: add opening stock, subtract closing stock (counts from Stew/Warehouse)
    inv_adj = {}
    for d, cls in (("food", "food_cost"), ("beverage", "beverage_cost")):
        o = db.execute("SELECT value_cents FROM acct_inventory_counts WHERE department=? AND count_date=? ORDER BY id DESC",
                       (d, (date.fromisoformat(start) - timedelta(days=1)).isoformat())).fetchone()
        c = db.execute("SELECT value_cents FROM acct_inventory_counts WHERE department=? AND count_date=? ORDER BY id DESC",
                       (d, end)).fetchone()
        if o and c and cls in by_class:
            adj = o[0] - c[0]
            inv_adj[cls] = adj
            by_class[cls] += adj
            by_group["COGS"] += adj
            dept.setdefault(d, {"Revenue": 0, "COGS": 0, "Labor": 0, "Contra revenue": 0})["COGS"] += adj
    gross = by_group["Revenue"]
    net = gross - by_group["Contra revenue"]
    labor, cogs = by_group["Labor"], by_group["COGS"]
    m = {
        "gross_sales": gross, "net_sales": net, "cogs": cogs, "labor": labor,
        "occupancy": by_group["Occupancy"], "operating": by_group["Operating"],
        "prime_cost": cogs + labor,
        "operating_profit": net - cogs - labor - by_group["Occupancy"] - by_group["Operating"],
        "food_cost_pct": _pct(by_class.get("food_cost", 0), by_class.get("food_sales", 0)),
        "beverage_cost_pct": _pct(by_class.get("beverage_cost", 0), by_class.get("beverage_sales", 0)),
        "labor_pct": _pct(labor, net), "prime_cost_pct": _pct(cogs + labor, net),
        "occupancy_pct": _pct(by_group["Occupancy"], net),
        "operating_margin_pct": _pct(net - cogs - labor - by_group["Occupancy"] - by_group["Operating"], net),
        "capital_spend": by_group["Capital"], "balance_sheet": by_group["Balance sheet"],
    }
    waste = db.execute("SELECT COALESCE(SUM(amount_cents),0) FROM acct_memos WHERE kind='waste' AND memo_date BETWEEN ? AND ?",
                       (start, end)).fetchone()[0]
    m["waste_memo"] = waste
    m["waste_pct_of_food_cost"] = _pct(waste, by_class.get("food_cost", 0))
    departments = {}
    for d, s in dept.items():
        if d == "shared":
            continue
        contrib = s["Revenue"] - s["Contra revenue"] - s["COGS"] - s["Labor"]
        departments[d] = {"sales": s["Revenue"], "cogs": s["COGS"], "direct_labor": s["Labor"],
                          "contribution": contrib, "contribution_pct": _pct(contrib, s["Revenue"] - s["Contra revenue"])}
    shared = dept.get("shared", {"Labor": 0, "COGS": 0})
    return {"period": (start, end), "metrics": m, "by_class": by_class, "departments": departments,
            "shared_labor": shared["Labor"], "shared_cogs": shared["COGS"],
            "unconfirmed": {"count": unconfirmed[0], "amount": unconfirmed[1]}, "inventory_adjustment": inv_adj}


def CLASSES_GROUPS():
    return sorted({g for g, _ in CLASSES.values()})


def _target_note(db, metric, value):
    lo, hi = targets(db).get(metric, (None, None))
    if value is None or (lo is None and hi is None):
        return ""
    if hi is not None and value > hi:
        return f"  ABOVE target {hi:g}%"
    if lo is not None and value < lo:
        return f"  below target {lo:g}% (check portions/recording)"
    return "  in range"


def report(db, member, start, end):
    """Full view for Finance+, kitchen percentages for Managers, refused below that. Every read is logged."""
    def full():
        return _report_text(db, numbers(db, start, end), full=True)

    def kitchen():
        return _report_text(db, numbers(db, start, end), full=False) if level(db, member) >= 2 else None

    out = read(db, member, "financial_block", full, kitchen)
    if out is None:
        raise PermissionError(f"the accounting view needs Manager (2) for kitchen percentages or Finance (3); "
                              f"{member} is level {level(db, member)}")
    return out


def _report_text(db, n, full):
    m, L = n["metrics"], []
    s, e = n["period"]
    if not full:
        L.append(f"Kitchen numbers {s} to {e} (percentages only; dollars and payroll need Finance level)")
        for k in ("food_cost_pct", "beverage_cost_pct", "labor_pct", "prime_cost_pct", "waste_pct_of_food_cost"):
            L.append(f"   {k.replace('_pct', ' %').replace('_', ' '):24} {m[k]}%" + _target_note(db, k, m[k]))
        return "\n".join(L)
    L.append(f"P&L view {s} to {e} (confirmed entries only)")
    L.append(f"   Gross sales            {_money(m['gross_sales']):>12}")
    L.append(f"   Refunds/comps          {_money(-n['by_class'].get('refunds_comps', 0)):>12}")
    L.append(f"   Net sales              {_money(m['net_sales']):>12}")
    for grp in ("COGS", "Labor", "Occupancy", "Operating"):
        L.append(f"   {grp:22} {_money(-sum(v for c, v in n['by_class'].items() if CLASSES[c][0] == grp)):>12}")
        for c, v in sorted(n["by_class"].items()):
            if CLASSES[c][0] == grp:
                L.append(f"      {c:19} {_money(v):>12}")
    for c, adj in n["inventory_adjustment"].items():
        L.append(f"      ({c} includes {_money(adj)} for stock change: opening count minus closing count)")
    missing = [c for c in ("food_cost", "beverage_cost") if c in n["by_class"] and c not in n["inventory_adjustment"]]
    if missing:
        L.append(f"      ({', '.join(missing)} = purchases only; no opening/closing count for this period, so delivery timing can swing it)")
    L.append(f"   Operating profit       {_money(m['operating_profit']):>12}  ({m['operating_margin_pct']}% of net sales)")
    L.append("   Crossed with revenue:")
    for k in ("food_cost_pct", "beverage_cost_pct", "labor_pct", "prime_cost_pct", "occupancy_pct"):
        L.append(f"      {k.replace('_pct', ' %').replace('_', ' '):21} {m[k]}%" + _target_note(db, k, m[k]))
    L.append(f"   Prime cost (COGS + labor) {_money(m['prime_cost'])}")
    if m["waste_memo"]:
        L.append(f"   Memo: waste at cost {_money(m['waste_memo'])} = {m['waste_pct_of_food_cost']}% of food cost "
                 "(already inside food cost, not added again)")
    if m["capital_spend"]:
        L.append(f"   Not in P&L: capital spend {_money(m['capital_spend'])} (accountant sets depreciation)")
    L.append("   By department (sales minus its own COGS and labor):")
    for d, v in sorted(n["departments"].items()):
        L.append(f"      {d:9} sales {_money(v['sales']):>11}  cogs {_money(v['cogs']):>10}  labor {_money(v['direct_labor']):>10}"
                 f"  contribution {_money(v['contribution']):>11} ({v['contribution_pct']}%)")
    L.append(f"      shared labor (managers, payroll tax) {_money(n['shared_labor'])} and shared COGS {_money(n['shared_cogs'])} "
             "are not split across departments")
    if (date.fromisoformat(e) + timedelta(days=1)).day != 1:
        L.append("   Partial month: rent and utilities post in full on the 1st, so occupancy % reads high until month end")
    u = n["unconfirmed"]
    if u["count"]:
        L.append(f"   Not counted yet: {u['count']} entr{'y' if u['count'] == 1 else 'ies'}, {_money(u['amount'])}, waiting on a person")
    return "\n".join(L)


def compare(db, member, p1, p2):
    """Period over period. Flags cost % moves of 2+ points and names the class that moved most."""
    if level(db, member) < 3:
        raise PermissionError("period comparison shows dollars; needs Finance level (3)")
    a, b = numbers(db, *p1), numbers(db, *p2)
    L = [f"{p1[0]}..{p1[1]}  vs  {p2[0]}..{p2[1]}"]
    for k in ("net_sales", "food_cost_pct", "beverage_cost_pct", "labor_pct", "prime_cost_pct",
              "occupancy_pct", "operating_margin_pct"):
        x, y = a["metrics"][k], b["metrics"][k]
        if k == "net_sales":
            L.append(f"   net sales           {_money(x):>12} -> {_money(y):>12}")
            continue
        move = None if x is None or y is None else round(y - x, 1)
        flag = ""
        if move is not None and abs(move) >= MOVE_FLAG_POINTS and k != "operating_margin_pct":
            flag = "  FLAG"
            db.execute("INSERT INTO acct_flags(period, kind, detail) VALUES (?,?,?)",
                       (f"{p2[0]}..{p2[1]}", "metric_move", f"{k} {x}% -> {y}%"))
        L.append(f"   {k.replace('_pct', ' %').replace('_', ' '):19} {x}% -> {y}%  ({move:+.1f} pts){flag}")
    # driver: each class as % of net sales, biggest change
    def share(n):
        return {c: 100 * v / n["metrics"]["net_sales"] for c, v in n["by_class"].items()
                if CLASSES[c][0] in ("COGS", "Labor", "Occupancy", "Operating")}
    sa, sb = share(a), share(b)
    moves = sorted(((sb.get(c, 0) - sa.get(c, 0), c) for c in set(sa) | set(sb)), key=lambda x: -abs(x[0]))
    for mv, c in moves[:3]:
        L.append(f"   driver: {c} {sa.get(c, 0):.1f}% -> {sb.get(c, 0):.1f}% of net sales ({mv:+.1f} pts)")
    return "\n".join(L)


# ---------- QuickBooks ----------
def record_inventory_count(db, member, count_date, department, value_cents):
    if level(db, member) < 2:
        raise PermissionError("inventory values show cost; needs Manager level (2)")
    db.execute("INSERT INTO acct_inventory_counts(count_date, department, value_cents, counted_by) VALUES (?,?,?,?)",
               (count_date, department, value_cents, member))


def import_qb(db, member, period, lines):
    """lines: [(qb_account, hiwr_class, amount_cents)]. The account->class map is set once with the accountant."""
    _need_finance(db, member, "importing QuickBooks")
    for acct, cls, amt in lines:
        db.execute("INSERT INTO qb_pl_lines(period, qb_account, class, amount_cents, imported_by) VALUES (?,?,?,?,?)",
                   (period, acct, cls, amt, member))


def reconcile_qb(db, member, period, start, end):
    """QuickBooks wins. HiWR lists each class that disagrees by more than $5 and 1%, with what could explain it."""
    _need_finance(db, member, "reconciling")
    n = numbers(db, start, end)
    qb = {}
    for r in db.execute("SELECT class, SUM(amount_cents) a FROM qb_pl_lines WHERE period=? GROUP BY class", (period,)):
        qb[r["class"]] = r["a"]
    out = []
    for c in sorted(set(qb) | set(n["by_class"])):
        h, q = n["by_class"].get(c, 0), qb.get(c, 0)
        gap = h - q
        if abs(gap) > 500 and abs(gap) > 0.01 * max(abs(q), 1):
            hints = []
            for t, e in _rows(db, start, end):
                if e["status"] in ("needs_person", "proposed") and (e["class"] == c or e["class"] in ("unclassified", "duplicate?")) \
                        and abs(t["amount_cents"] - abs(gap)) <= 100:
                    hints.append(f"#{t['id']} {t['counterparty']} {_money(t['amount_cents'])} is still {e['status']}")
            if not hints:
                hints.append("no HiWR entry explains it; it was entered only in QuickBooks (a journal entry or a bill paid outside HiWR), so add the source record to HiWR or ask the bookkeeper")
            detail = f"{c}: HiWR {_money(h)} vs QuickBooks {_money(q)} (gap {_money(gap)}). " + "; ".join(hints)
            db.execute("INSERT INTO acct_flags(period, kind, detail) VALUES (?,?,?)", (period, "qb_mismatch", detail))
            out.append(detail)
    return out or ["HiWR matches QuickBooks for every class"]
