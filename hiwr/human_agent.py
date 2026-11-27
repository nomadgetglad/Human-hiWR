"""Human, grown in stages into an accounting and task list agent.

  0 Pioneer           records only, while the pioneering phase runs
  1 Bookkeeper        proposes classes; a Finance person confirms every entry
  2 Accounting agent  confirms routine entries on its own (POS sales, timeclock wages and tax accruals, sales tax,
                      payees a person has confirmed 3+ times with no correction) under a dollar limit; 1 in 10 goes
                      to Finance as an audit sample; one bad sample sends that payee back to manual
  3 Task list agent   turns what it finds into the day's tasks for the right person, assigned in the owner's name

At every stage Human never moves money, sends a PO, picks a margin option, switches a vendor or changes access.
Each stage is turned on by the account holder, and only once the one before it has earned it.
"""
from datetime import date
from urllib.parse import urlparse
from hiwr_access import level
from hiwr_contacts import _is_owner, assign
from hiwr_accounting import _latest, _event, _money, CLASSES
from hiwr_processes import open_ticket
from hiwr_pioneer import status as pioneer_status

STAGES = {0: "Pioneer", 1: "Bookkeeper", 2: "Accounting agent", 3: "Task list agent"}
AUTO_LIMIT_CENTS = 1500_00
AUDIT_EVERY = 10
STAGE2_MIN_CONFIRMED = 200
STAGE2_MAX_CORRECTION_PCT = 5.0
QUOTE_MAX_AGE_DAYS = 7
MIN_SAVING_PCT = 5.0
AGENT = "human_agent"


# ---------- stages ----------
def stage(db):
    r = db.execute("SELECT stage FROM human_stage_events ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else 0


def _people_track_record(db):
    rows = db.execute("SELECT e.txn_id, e.status, e.by, e.reason FROM acct_class_events e WHERE e.status='confirmed' "
                      "AND e.by<>?", (AGENT,)).fetchall()
    corrected = sum(1 for r in rows if r["reason"] and r["reason"].startswith("corrected"))
    return len(rows), (100 * corrected / len(rows) if rows else 100.0)


def set_stage(db, owner, n, reason=None):
    if not _is_owner(db, owner):
        raise PermissionError("only the account holder changes what Human is allowed to do")
    if n >= 1 and not pioneer_status(db)["done"]:
        raise PermissionError(f"pioneering isn't done yet: {pioneer_status(db)['next']}")
    if n >= 2:
        count, pct = _people_track_record(db)
        if count < STAGE2_MIN_CONFIRMED or pct > STAGE2_MAX_CORRECTION_PCT:
            raise PermissionError(f"stage 2 needs {STAGE2_MIN_CONFIRMED}+ entries confirmed by people with under "
                                  f"{STAGE2_MAX_CORRECTION_PCT:g}% corrected; so far {count} with {pct:.1f}% corrected")
    if n >= 3 and stage(db) < 2:
        raise PermissionError("stage 3 comes after Human has run as the accounting agent")
    db.execute("INSERT INTO human_stage_events(stage, set_by, reason) VALUES (?,?,?)", (n, owner, reason))
    return f"Human is now stage {n}: {STAGES[n]}"


# ---------- stage 2: automatic classification ----------
def _routine(db, t, e):
    if t["source"] in ("pos", "timeclock", "timeclock_tax", "pos_tax"):
        return True, f"routine {t['source']} entry"
    if t["amount_cents"] > AUTO_LIMIT_CENTS or not t["counterparty"]:
        return False, None
    if db.execute("SELECT 1 FROM auto_blocks WHERE lower(counterparty)=lower(?)", (t["counterparty"],)).fetchone():
        return False, None
    prior = db.execute("SELECT e.class, e.reason FROM acct_class_events e JOIN acct_txns x ON x.id=e.txn_id "
                       "WHERE lower(x.counterparty)=lower(?) AND e.status='confirmed' AND e.by<>?",
                       (t["counterparty"], AGENT)).fetchall()
    same = [p for p in prior if p["class"] == e["class"]]
    if len(same) >= 3 and not any((p["reason"] or "").startswith("corrected") for p in prior):
        return True, f"{t['counterparty']} confirmed {len(same)}x as {e['class']} by people"
    return False, None


def auto_classify(db, start, end):
    if stage(db) < 2:
        raise PermissionError(f"Human is stage {stage(db)} ({STAGES[stage(db)]}); auto-confirm starts at stage 2")
    done, left, n = [], [], db.execute("SELECT COUNT(*) FROM auto_audit").fetchone()[0]
    for t in db.execute("SELECT * FROM acct_txns WHERE txn_date BETWEEN ? AND ? ORDER BY id", (start, end)).fetchall():
        e = _latest(db, t["id"])
        if e["status"] != "proposed":
            if e["status"] == "needs_person":
                left.append(t)
            continue
        ok, why = _routine(db, t, e)
        if not ok:
            left.append(t)
            continue
        _event(db, t["id"], e["class"], e["department"], "confirmed", AGENT, f"auto (stage 2): {why}")
        done.append(t)
        n += 1
        first_for_payee = t["counterparty"] and t["source"] not in ("pos", "timeclock", "timeclock_tax", "pos_tax") and not \
            db.execute("SELECT 1 FROM auto_audit a JOIN acct_txns x ON x.id=a.txn_id WHERE x.counterparty=?",
                       (t["counterparty"],)).fetchone()
        if n % AUDIT_EVERY == 0 or first_for_payee:     # every 10th, and the first one per payee
            db.execute("INSERT INTO auto_audit(txn_id) VALUES (?)", (t["id"],))
    return {"auto_confirmed": len(done), "left_for_people": len(left),
            "audit_samples": db.execute("SELECT COUNT(*) FROM auto_audit WHERE result IS NULL").fetchone()[0]}


def audit(db, member, audit_id, ok, correct_class=None):
    if level(db, member) < 3:
        raise PermissionError("audit samples are checked by Finance (3)")
    a = db.execute("SELECT * FROM auto_audit WHERE id=?", (audit_id,)).fetchone()
    t = db.execute("SELECT * FROM acct_txns WHERE id=?", (a["txn_id"],)).fetchone()
    db.execute("UPDATE auto_audit SET result=?, checked_by=? WHERE id=?", ("ok" if ok else f"wrong: {correct_class}", member, audit_id))
    if not ok:
        _event(db, t["id"], correct_class, None, "confirmed", member, f"corrected from auto ({_latest(db, t['id'])['class']})")
        if t["counterparty"]:
            db.execute("INSERT OR REPLACE INTO auto_blocks(counterparty, blocked_by, reason) VALUES (?,?,?)",
                       (t["counterparty"], member, f"auto got #{t['id']} wrong"))
        return f"#{t['id']} corrected to {correct_class}; {t['counterparty']} goes back to manual review"
    return f"#{t['id']} ok"


def tax_summary(db, member, start, end):
    """Tax records: what was collected or accrued this period, what was paid, and the running balance owed now.
    Payments usually cover the previous period, so the balance is counted from the start of the books."""
    if level(db, member) < 3:
        raise PermissionError("tax records need Finance level (3)")

    def total(cls, a, b, payee=None):
        q = ("SELECT COALESCE(SUM(t.amount_cents),0) FROM acct_txns t WHERE t.txn_date BETWEEN ? AND ? AND "
             "(SELECT class FROM acct_class_events WHERE txn_id=t.id ORDER BY id DESC LIMIT 1)=?")
        args = [a, b, cls]
        if payee:
            q += " AND lower(t.counterparty) LIKE ?"
            args.append(f"%{payee}%")
        return db.execute(q, args).fetchone()[0]
    first = db.execute("SELECT MIN(txn_date) FROM acct_txns").fetchone()[0]
    L = [f"Tax records {start} to {end}"]
    for name, cls, payee in (("sales tax", "sales_tax_collected", "ncdor"), ("employer payroll tax", "payroll_tax", "eftps")):
        got, paid = total(cls, start, end), total("tax_payment", start, end, payee)
        owed = total(cls, first, end) - total("tax_payment", first, end, payee)
        L.append(f"   {name}: {'collected' if 'sales' in name else 'accrued'} {_money(got)} this period, paid {_money(paid)}; "
                 f"balance owed as of {end}: {_money(owed)}")
    L.append("   Employee withholding sits with the payroll provider. Filing due dates follow your schedule; confirm with the accountant.")
    return "\n".join(L)


# ---------- inventory order updates ----------
def po_event(db, po_id, event, at, by, material=None, qty=None, detail=None):
    db.execute("INSERT INTO po_events(po_id, event, material, qty, detail, at, by) VALUES (?,?,?,?,?,?,?)",
               (po_id, event, material, qty, detail, at, by))


def receive(db, po_id, received, invoice_unit_cents, at, by):
    """received: {material: units}; invoice_unit_cents: {material: cents per unit on the invoice}.
    Shorts and price differences open one SHIPMENT ticket with the expected credit."""
    po = db.execute("SELECT * FROM po_drafts WHERE id=?", (po_id,)).fetchone()
    issues, credit = [], 0
    for ln in db.execute("SELECT * FROM po_lines WHERE po_id=?", (po_id,)).fetchall():
        got = received.get(ln["material"], 0)
        unit_po = ln["cost_cents"] / ln["units"]
        if got < ln["units"]:
            po_event(db, po_id, "short", at, by, ln["material"], ln["units"] - got)
            issues.append(f"{ln['material']} short {ln['units'] - got:g}")
            credit += round((ln["units"] - got) * unit_po)
        inv = invoice_unit_cents.get(ln["material"])
        if inv and inv > unit_po * 1.02:
            po_event(db, po_id, "price_diff", at, by, ln["material"], got, f"{inv:.1f} vs {unit_po:.1f} c/unit")
            issues.append(f"{ln['material']} billed {inv / unit_po * 100 - 100:.0f}% over the PO price")
            credit += round((inv - unit_po) * got)
    if issues:
        tid = open_ticket(db, "SHIPMENT", f"PO #{po_id} {po['vendor']}: " + "; ".join(issues), 3, at, AGENT,
                          financial={"expected_credit_cents": credit, "status": "anticipated"})
        po_event(db, po_id, "ticket", at, AGENT, detail=tid)
    po_event(db, po_id, "received", at, by)
    return issues, credit


def order_updates(db, po_id):
    """Plain-language updates for the chef's chat, newest last."""
    po = db.execute("SELECT * FROM po_drafts WHERE id=?", (po_id,)).fetchone()
    out = []
    for e in db.execute("SELECT * FROM po_events WHERE po_id=? ORDER BY id", (po_id,)):
        when = e["at"][5:16].replace("T", " ")
        ev, d = e["event"], e["detail"]
        if ev == "confirmed":
            msg = f"{po['vendor']} confirmed PO #{po_id}" + (f": {d}" if d else "")
        elif ev == "shipped":
            msg = f"PO #{po_id} is on the truck" + (f", {d}" if d else "")
        elif ev == "substitution":
            msg = f"{po['vendor']} wants to substitute {e['material']}: {d}. Accept or refuse in Ordering"
        elif ev == "short":
            msg = f"{e['material']} came in short by {e['qty']:g}"
        elif ev == "price_diff":
            msg = f"{e['material']} billed above the PO price ({d})"
        elif ev == "ticket":
            msg = f"Opened {d} for the shorts and price differences; credit is pending, not booked"
        elif ev == "received":
            msg = f"PO #{po_id} received"
        else:
            msg = ev
        out.append(f"{when}  {msg}")
    return out


# ---------- vendor suggestions from approved price sources ----------
def approve_source(db, owner, domain, vendor, method, note=None):
    """method: 'account_export' (order guide download from the customer's own account), 'api' (vendor-provided
    integration), or 'catalog_file'. Prices come only through the business's own account and the vendor's terms."""
    if not _is_owner(db, owner):
        raise PermissionError("only the account holder approves where vendor prices come from")
    db.execute("INSERT OR REPLACE INTO approved_price_sources(domain, vendor, method, approved_by, note) VALUES (?,?,?,?,?)",
               (domain.lower(), vendor, method, owner, note))


def _source_for(db, url):
    u = urlparse(url)
    if u.scheme != "https":
        raise PermissionError(f"{url}: prices are only taken over https")
    host = (u.hostname or "").lower()
    for r in db.execute("SELECT * FROM approved_price_sources"):
        if host == r["domain"] or host.endswith("." + r["domain"]):
            return r
    raise PermissionError(f"{host} is not an approved price source; the account holder approves sources")


def ingest_quotes(db, url, rows, fetched_at, by):
    """rows: [{material, upc, pack_size, pack_label, pack_cost_cents, lead_days}] parsed from the approved source."""
    src = _source_for(db, url)
    for r in rows:
        db.execute("INSERT INTO price_quotes(vendor, material, upc, pack_size, pack_label, pack_cost_cents, unit_cost_cents, "
                   "lead_days, source_url, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (src["vendor"], r["material"], r.get("upc"), r["pack_size"], r.get("pack_label"), r["pack_cost_cents"],
                    r["pack_cost_cents"] / r["pack_size"], r.get("lead_days"), url, fetched_at))
    return f"{len(rows)} prices from {src['vendor']} ({src['method']})"


def suggest_vendors(db, today, weekly_use):
    """weekly_use: {material: units/week} from the Ordering forecast. Suggests only fresh quotes from approved
    sources, at least 5% cheaper, from vendors without an open SHIPMENT ticket."""
    out = []
    for ms in db.execute("SELECT * FROM material_sources WHERE preferred=1").fetchall():
        mat, cur_v = ms["material"], ms["vendor"]
        fresh = [q for q in db.execute("SELECT * FROM price_quotes WHERE material=? ORDER BY id DESC", (mat,))
                 if (date.fromisoformat(today) - date.fromisoformat(q["fetched_at"][:10])).days <= QUOTE_MAX_AGE_DAYS]
        latest = {}
        for q in fresh:
            latest.setdefault(q["vendor"], q)
        cur_cost = latest[cur_v]["unit_cost_cents"] if cur_v in latest else ms["unit_cost_cents"]
        for v, q in sorted(latest.items(), key=lambda kv: kv[1]["unit_cost_cents"]):
            if v == cur_v:
                continue
            saving = 100 * (cur_cost - q["unit_cost_cents"]) / cur_cost
            if saving < MIN_SAVING_PCT:
                break
            if db.execute("SELECT 1 FROM tickets WHERE type='SHIPMENT' AND status<>'closed' AND subject LIKE ?",
                          (f"%{v}%",)).fetchone():
                continue
            weekly = round((cur_cost - q["unit_cost_cents"]) * weekly_use.get(mat, 0))
            detail = (f"{mat}: {cur_v} {cur_cost:.1f}c/unit vs {v} {q['unit_cost_cents']:.1f}c/unit "
                      f"({q['pack_label']}, {q['lead_days']}-day lead), about {_money(weekly)}/week at current use. "
                      "Check quality and delivery days before switching")
            sid = db.execute("INSERT INTO vendor_suggestions(material, from_vendor, to_vendor, saving_pct, weekly_saving_cents, detail) "
                             "VALUES (?,?,?,?,?,?)", (mat, cur_v, v, round(saving, 1), weekly, detail)).lastrowid
            out.append((sid, detail))
            break
    return out


def decide_vendor(db, member, suggestion_id, accept, note=None):
    if level(db, member) < 2:
        raise PermissionError("switching vendors needs Manager level (2)")
    s = db.execute("SELECT * FROM vendor_suggestions WHERE id=?", (suggestion_id,)).fetchone()
    db.execute("UPDATE vendor_suggestions SET status=?, decided_by=? WHERE id=?",
               ("accepted" if accept else "declined", member, suggestion_id))
    if accept:
        q = db.execute("SELECT * FROM price_quotes WHERE vendor=? AND material=? ORDER BY id DESC", (s["to_vendor"], s["material"])).fetchone()
        db.execute("INSERT OR IGNORE INTO material_sources(material, vendor, lead_days, unit_cost_cents) VALUES (?,?,?,?)",
                   (s["material"], s["to_vendor"], q["lead_days"] or 2, round(q["unit_cost_cents"])))
        db.execute("UPDATE material_sources SET preferred = (vendor=?) WHERE material=?", (s["to_vendor"], s["material"]))
    return f"{s['material']}: {'switched to ' + s['to_vendor'] if accept else 'kept ' + s['from_vendor']} ({member})"


# ---------- stage 3: the day's task list ----------
def build_tasks(db, owner, day, people):
    """people: {"finance": member, "manager": member, "chef": member}. Assigned in the owner's name, marked as drafted by Human."""
    if stage(db) < 3:
        raise PermissionError(f"Human is stage {stage(db)} ({STAGES[stage(db)]}); task lists start at stage 3")
    made = []

    def add(role, kind, title, goal=None):
        aid = assign(db, owner, people[role], kind, f"[Human] {title}", due_on=day, goal=goal)
        made.append((people[role], kind, title))
        return aid

    need = [t for t in db.execute("SELECT * FROM acct_txns").fetchall() if _latest(db, t["id"])["status"] == "needs_person"]
    if need:
        add("finance", "task", f"Classify {len(need)} entr{'y' if len(need) == 1 else 'ies'} Human couldn't settle ({_money(sum(t['amount_cents'] for t in need))})")
    audits = db.execute("SELECT COUNT(*) FROM auto_audit WHERE result IS NULL").fetchone()[0]
    if audits:
        add("finance", "task", f"Check {audits} audit samples of what Human confirmed on its own")
    for r in db.execute("SELECT * FROM margin_choices"):
        role = {"waste": "chef", "food_cost": "manager", "labor": "manager", "beverage_attach": "manager", "price": "manager"}[r["option"]]
        add(role, "analysis", f"Margin plan {r['sample']}: {r['option'].replace('_', ' ')}: {r['note']}",
            goal=f"recover {_money(r['expected_cents'])} next sample")
    for s in db.execute("SELECT * FROM vendor_suggestions WHERE status='proposed'"):
        add("manager", "call", f"Vendor price check: {s['detail']}")
    for e in db.execute("SELECT * FROM po_events WHERE event='ticket'"):
        add("chef", "call", f"Call the vendor about {e['detail']} and confirm the credit")
    return made
