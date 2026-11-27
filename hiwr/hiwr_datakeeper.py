"""Data keeper: Human checks that the databases agree with each other and stay organized.

It runs read-only checks across tables and writes findings with a proposed fix. It never deletes or rewrites:
records are append-only, so a fix is a correcting entry, a merge, or a task for a person, applied after a yes.
"""
import json
from datetime import date, timedelta
from hiwr_contract import CONTRACT_VERSION
from hiwr_contacts import _is_owner

APPEND_ONLY = ["contact_events", "order_stage_events", "access_log", "acct_txns", "acct_class_events", "signin_events"]
STALE_PROPOSED_DAYS = 7
EXPECTED_CONTRACT = CONTRACT_VERSION


def _has(db, table):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def checks(db, today):
    f = []

    def add(name, sev, detail, fix):
        f.append((name, sev, detail, fix))

    # 1. append-only protection is still in place
    trig = {r[0] for r in db.execute("SELECT tbl_name FROM sqlite_master WHERE type='trigger' AND sql LIKE '%BEFORE DELETE%'")}
    for t in APPEND_ONLY:
        if _has(db, t) and t not in trig:
            add("append_only", "high", f"{t} has lost its no-delete guard", "restore the trigger from schema.sql (owner)")

    # 2. same phone on two client records that were never merged
    for r in db.execute("SELECT e164, GROUP_CONCAT(c.id) ids, GROUP_CONCAT(c.display_name, ' / ') names FROM contact_phones p "
                        "JOIN contacts c ON c.id=p.contact_id WHERE c.merged_into IS NULL GROUP BY e164 HAVING COUNT(DISTINCT c.id) > 1"):
        add("duplicate_clients", "medium", f"{r['names']} share {r['e164']} (records {r['ids']})",
            f"merge into the oldest record, keeping both histories (merge_contacts {r['ids']})")

    # 3. sales days: POS lines exist but the day's sales aren't in the books, or the reverse
    if _has(db, "dish_sales"):
        pos_days = {r[0] for r in db.execute("SELECT DISTINCT day FROM dish_sales")}
        book_days = {r[0] for r in db.execute("SELECT DISTINCT txn_date FROM acct_txns WHERE source='pos'")}
        for d in sorted(pos_days - book_days):
            add("sales_not_in_books", "high", f"{d}: item sales exist but no sales posted to the books", "re-run the POS import for that day")
        start = min(book_days | pos_days) if (book_days | pos_days) else today
        d = date.fromisoformat(start)
        while d < date.fromisoformat(today):
            if d.isoformat() not in book_days | pos_days and d.weekday() != 6:
                add("missing_sales_day", "medium", f"{d}: no sales recorded at all (not a Sunday)", "enter the Z-report, or mark the day closed")
            d += timedelta(days=1)

    # 4. received orders with no invoice in the books, and invoices with no order
    if _has(db, "po_events"):
        for r in db.execute("SELECT DISTINCT p.id, p.vendor, e.at FROM po_drafts p JOIN po_events e ON e.po_id=p.id AND e.event='received'"):
            day = r["at"][:10]
            hit = db.execute("SELECT 1 FROM acct_txns WHERE counterparty=? AND source='invoice' AND abs(julianday(txn_date)-julianday(?))<=3",
                             (r["vendor"], day)).fetchone()
            if not hit:
                add("received_no_invoice", "medium", f"PO #{r['id']} {r['vendor']} received {day}, no invoice on the record",
                    "snap the invoice on HiWR In, or ask the vendor for it")

    # 5. tickets that say a credit is expected, with no credit posted after 14 days
    for r in db.execute("SELECT * FROM tickets WHERE financial IS NOT NULL AND status<>'closed'"):
        fin = json.loads(r["financial"])
        age = (date.fromisoformat(today) - date.fromisoformat(r["opened_at"][:10])).days
        if fin.get("expected_credit_cents") and age >= 14:
            add("credit_overdue", "medium", f"{r['id']} expects a credit of ${fin['expected_credit_cents'] / 100:,.2f}, open {age} days",
                "call the vendor (task to the manager)")

    # 6. ledger entries left unclassified too long
    if _has(db, "acct_class_events"):
        for r in db.execute("SELECT t.id, t.txn_date, t.counterparty, t.amount_cents FROM acct_txns t WHERE "
                            "(SELECT status FROM acct_class_events WHERE txn_id=t.id ORDER BY id DESC LIMIT 1) IN ('proposed','needs_person')"):
            if (date.fromisoformat(today) - date.fromisoformat(r["txn_date"])).days > STALE_PROPOSED_DAYS:
                add("stale_unclassified", "low", f"#{r['id']} {r['counterparty']} ${r['amount_cents'] / 100:,.2f} from {r['txn_date']} still not confirmed",
                    "goes on Finance's task list")

    # 7. one name, two spellings of a vendor
    vend = {}
    for r in db.execute("SELECT DISTINCT counterparty FROM acct_txns WHERE counterparty IS NOT NULL"):
        key = "".join(ch for ch in r[0].lower() if ch.isalnum()).replace("inc", "").replace("co", "")
        vend.setdefault(key, set()).add(r[0])
    for names in vend.values():
        if len(names) > 1:
            add("vendor_spelling", "low", f"one vendor under {len(names)} names: {', '.join(sorted(names))}",
                "pick one name; HiWR adds a rule so new entries use it")

    # 8. stock on hand below zero (a sale or prep logged without a receipt)
    if _has(db, "materials"):
        for r in db.execute("SELECT material, on_hand, unit FROM materials WHERE on_hand < 0"):
            add("negative_stock", "medium", f"{r['material']} shows {r['on_hand']:g} {r['unit']}", "count it; a delivery is probably missing")

    # 9. contract versions: HiWR and Human built together
    snap_v = db.execute("SELECT value FROM settings WHERE key='human_contract'").fetchone() if _has(db, "settings") else None
    if snap_v and snap_v[0] != EXPECTED_CONTRACT:
        add("contract_mismatch", "high", f"Human is on contract {snap_v[0]}, HiWR on {EXPECTED_CONTRACT}", "update both together")
    return f


def run(db, today, run_at):
    found = checks(db, today)
    for name, sev, detail, fix in found:
        if not db.execute("SELECT 1 FROM datakeeper_findings WHERE detail=? AND status='open'", (detail,)).fetchone():
            db.execute("INSERT INTO datakeeper_findings(run_at, check_name, severity, detail, proposed_fix) VALUES (?,?,?,?,?)",
                       (run_at, name, sev, detail, fix))
    rows = db.execute("SELECT * FROM datakeeper_findings WHERE status='open' ORDER BY "
                      "CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, id").fetchall()
    return rows


def decide(db, member, finding_id, accept):
    if not _is_owner(db, member):
        from hiwr_access import level
        if level(db, member) < 3:
            raise PermissionError("data fixes are approved by Finance (3) or the account holder")
    db.execute("UPDATE datakeeper_findings SET status=?, decided_by=? WHERE id=?",
               ("fix approved" if accept else "dismissed", member, finding_id))
