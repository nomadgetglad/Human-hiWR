"""The tools the model can call. Every access, privacy and approval rule is checked here, in code.

The model never gets a tool that approves, pays, sends, posts or writes to QuickBooks. Those tools don't
exist, so no prompt can make the model use them.
"""
import csv
import fnmatch
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "hiwr"))

from hiwr_access import level as member_level, LEVEL_NAMES  # noqa: E402
from hiwr_contacts import request_money_action, money_action_status, is_certified, _is_owner  # noqa: E402
from hiwr_processes import open_ticket as _open_ticket, close_ticket as _close_ticket, ticket_priority  # noqa: E402

from . import privacy  # noqa: E402

METHODS_DIR = HERE / "prompts" / "methods"
SCRIPTS_DIR = HERE / "scripts"
METHODS = sorted(p.stem for p in METHODS_DIR.glob("*.md"))

TICKET_STATUSES = ["new", "triaged", "awaiting_client", "awaiting_vendor", "awaiting_manager", "awaiting_approval",
                   "resolved_pending_financial_sync", "closed"]
MONEY_SCOPES = {"refund", "payroll", "fee", "subcontract"}            # money_actions in hiwr/schema.sql
OTHER_APPROVALS = {"purchase_order", "vendor_chargeback", "vendor_credit", "warranty_replacement", "reward",
                   "pay_change", "hire", "permission", "schedule_override", "write_up", "termination",
                   "end_of_day_signoff"}
PENDING = "PENDING - requires double approval"

# ---------- what each level may read in the database ----------
# Tables not listed need Manager (2). Columns listed separately are hidden (read as null) below their level.
TABLE_LEVEL = {
    **dict.fromkeys(["tickets", "ticket_events", "recipes", "recipe_lines", "material_allergens", "workflows",
                     "workflow_stages", "dining_tables", "sop_steps", "catalog", "prep_items", "prep_usage",
                     "shelf_plan", "agent_handoffs"], 0),
    **dict.fromkeys(["materials", "stock_moves", "material_moves", "staff_availability", "shifts", "shift_events",
                     "assignments", "assignment_events", "waitlist", "waitlist_events", "table_assignments",
                     "appointments", "appointment_events", "processes", "process_checkpoints", "process_readings",
                     "corrective_actions", "dish_sales", "prep_batches", "units", "unit_checks", "station_output",
                     "shipments", "products", "orders", "order_items", "order_stage_events", "material_par",
                     "staffing_rules", "production_quota", "bill_of_materials"], 1),
    **dict.fromkeys(["staff", "money_actions", "approvals", "acct_txns", "acct_class_events", "acct_rules",
                     "acct_memos", "acct_targets", "qb_pl_lines", "acct_flags", "acct_inventory_counts", "positions",
                     "lab_draws", "lab_timesheets", "lab_subcontracts", "work_agreements", "conduct_events",
                     "hire_decisions", "staff_booked_hours", "signed_decisions", "approver_keys",
                     "agent_proposals"], 3),
    **dict.fromkeys(["order_payments", "access_log", "member_levels", "task_grants", "task_revocations",
                     "plan_owners", "approver_certifications", "agent_log"], 4),
}
COLUMN_LEVEL = {("tickets", "financial"): 3, ("products", "cost_cents"): 2, ("material_sources", "cost_cents"): 2}
ROW_LIMIT = 200

_SQLITE_READ, _SQLITE_SELECT, _SQLITE_FUNCTION, _SQLITE_RECURSIVE = 20, 21, 31, 33


class Refused(Exception):
    """A rule said no. The message is safe to show the model and the person."""


def _now():
    return datetime.now().isoformat(timespec="minutes")


class Toolbox:
    def __init__(self, db, cfg, requester, people, clock=_now, db_path=None):
        self.db, self.cfg, self.requester, self.clock, self.db_path = db, cfg, requester, clock, db_path
        self.people = people
        self.level = member_level(db, requester)
        self.folder = Path(cfg["data"]["folder"]).resolve()
        self.verified_orders, self.failed_verifications = set(), 0
        self.turn_pending = []          # approval items filed this turn, so the reply can't leave them out
        self.calls = []                 # (tool, allowed) for the audit log
        self.loaded_methods = set()
        self._tools = {t["name"]: t for t in self._define()}

    # ---------- registry ----------
    def specs(self):
        """Tools this person's level may use. Higher-level tools aren't even offered."""
        return [{k: t[k] for k in ("name", "description", "parameters")} for t in self._tools.values()
                if self.level >= t["level"]]

    def call(self, name, args):
        t = self._tools.get(name)
        if t is None:
            self.calls.append((name, False))
            return {"error": f"there is no tool named {name}"}
        if self.level < t["level"]:
            self.calls.append((name, False))
            return {"refused": f"not available at {LEVEL_NAMES[self.level]} level; needs {LEVEL_NAMES[t['level']]}"}
        try:
            out = t["fn"](**(args or {}))
            self.calls.append((name, True))
        except Refused as e:
            self.calls.append((name, False))
            out = {"refused": str(e)}
        except (TypeError, ValueError, LookupError, PermissionError, sqlite3.Error) as e:
            self.calls.append((name, False))
            out = {"error": str(e)}
        return out

    def _define(self):
        S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req),  # noqa: E731
                                   "additionalProperties": False}
        s, i, n, b = {"type": "string"}, {"type": "integer"}, {"type": "number"}, {"type": "boolean"}
        return [
            dict(name="load_method", level=0, fn=self.load_method,
                 description="Load the detailed method for a kind of work. Call before doing that work.",
                 parameters=S({"name": {"type": "string", "enum": METHODS}}, ["name"])),
            dict(name="list_files", level=0, fn=self.list_files,
                 description="List files in the hiWR data folder (names only). Optional glob pattern like '*.csv'.",
                 parameters=S({"pattern": s})),
            dict(name="read_file", level=0, fn=self.read_file,
                 description="Read a text file (csv, txt, md, json) from the hiWR data folder.",
                 parameters=S({"path": s}, ["path"])),
            dict(name="query_db", level=0, fn=self.query_db,
                 description="Run one read-only SELECT on the hiWR database. Tables or columns above your level are "
                             f"refused or read as null. At most {ROW_LIMIT} rows. Use 'SELECT name FROM sqlite_master' "
                             "to see tables.",
                 parameters=S({"sql": s}, ["sql"])),
            dict(name="list_tickets", level=0, fn=self.list_tickets,
                 description="Ticket queue by priority with age and status. Optional status filter; 'open' = not closed.",
                 parameters=S({"status": s})),
            dict(name="get_ticket", level=0, fn=self.get_ticket,
                 description="One ticket with its fields and full event history.",
                 parameters=S({"ticket_id": s}, ["ticket_id"])),
            dict(name="open_ticket", level=0, fn=self.open_ticket,
                 description="Open a DISPUTE or SHIPMENT ticket. Priority is computed. Check list_tickets first so you "
                             "don't open a duplicate. fields holds subtype, client_ref, order_id, vendor_ref, upc, "
                             "summary, next_step, due_by and the rest; financial holds the financial block "
                             "(Finance level only, ignored below).",
                 parameters=S({"type": {"type": "string", "enum": ["DISPUTE", "SHIPMENT"]}, "subject": s,
                               "dollars": n, "perishable": b, "party": s, "fields": {"type": "object"},
                               "financial": {"type": "object"}}, ["type", "subject"])),
            dict(name="update_ticket_status", level=1, fn=self.update_ticket_status,
                 description="Move a ticket to a new status. Closing is refused while money on it is pending or unposted.",
                 parameters=S({"ticket_id": s, "status": {"type": "string", "enum": TICKET_STATUSES}, "note": s},
                              ["ticket_id", "status"])),
            dict(name="add_ticket_note", level=0, fn=self.add_ticket_note,
                 description="Append a note or updated fields to a ticket's history (never overwrites).",
                 parameters=S({"ticket_id": s, "note": s, "fields": {"type": "object"}}, ["ticket_id", "note"])),
            dict(name="propose_money_action", level=2, fn=self.propose_money_action,
                 description="File a money or authority item for double approval. Money scopes: refund, payroll, fee, "
                             "subcontract. Other kinds: " + ", ".join(sorted(OTHER_APPROVALS)) + ". It is always "
                             "PENDING; you cannot approve it. Returns who must approve.",
                 parameters=S({"kind": s, "amount": n, "currency": s, "description": s, "ticket_id": s},
                              ["kind", "description"])),
            dict(name="verify_sale", level=0, fn=self.verify_sale,
                 description="Check an order # against the last 4 digits of the card used. Returns verified or not.",
                 parameters=S({"order_id": i, "card_last4": s}, ["order_id", "card_last4"])),
            dict(name="get_order", level=0, fn=self.get_order,
                 description="Hub view of one order: items, stages, appointment, shipment, tickets. Needs verify_sale "
                             "first below Manager level.",
                 parameters=S({"order_id": i}, ["order_id"])),
            dict(name="get_business_contacts", level=0, fn=self.get_business_contacts,
                 description="Business phone, text number, hours and on-duty manager, as the owner entered them.",
                 parameters=S({})),
            dict(name="request_live_person", level=0, fn=self.request_live_person,
                 description="Queue a call, callback or text with a person. Nothing is sent automatically. "
                             "route_to customer_service is the default for an employee who needs human support "
                             "from the business (pay, schedule, HR, account, equipment); it shows in the app's "
                             "Customer service section.",
                 parameters=S({"channel": {"type": "string", "enum": ["call", "callback", "text"]},
                               "route_to": {"type": "string", "enum": ["customer_service", "on_duty_manager", "owner",
                                                                       "approvers"]},
                               "reason": s, "contact": s, "best_time": s, "ticket_id": s}, ["channel", "route_to"])),
            dict(name="write_draft", level=0, fn=self.write_draft,
                 description="Save a draft (message, notice, summary, JSON) to the data folder's drafts/ for a person "
                             "to review and send. Never sends anything.",
                 parameters=S({"filename": s, "content": s}, ["filename", "content"])),
            dict(name="quickbooks_read", level=3, fn=self.quickbooks_read,
                 description="Read QuickBooks lines if connected (read only, there is no write). Optional text filter.",
                 parameters=S({"contains": s})),
            dict(name="run_script", level=3, fn=self.run_script,
                 description="Run one of the owner-approved scripts in agent/scripts/. Optional args list.",
                 parameters=S({"name": {"type": "string", "enum": sorted(p.stem for p in SCRIPTS_DIR.glob("*.py"))},
                               "args": {"type": "array", "items": s}}, ["name"])),
        ]

    # ---------- helpers ----------
    def _scrub(self, obj):
        return json.loads(privacy.scrub(json.dumps(obj, default=str), self.people))

    def _actor(self):
        return f"{self.requester} via assistant"

    def _ticket(self, tid):
        t = self.db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
        if t is None:
            raise LookupError(f"no ticket {tid}")
        return t

    def _event(self, tid, event, detail):
        self.db.execute("INSERT INTO ticket_events(ticket_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
                        (tid, self.clock(), self._actor(), event, detail))

    # ---------- methods and files ----------
    def load_method(self, name):
        if name not in METHODS:
            raise LookupError(f"no method {name}; methods are {', '.join(METHODS)}")
        self.loaded_methods.add(name)
        return {"method": name, "text": (METHODS_DIR / f"{name}.md").read_text(encoding="utf-8")}

    def _file_level(self, rel):
        lvl = 0
        for pat, need in self.cfg["files"]["levels"].items():
            if fnmatch.fnmatch(rel.lower(), pat.lower()) or fnmatch.fnmatch(Path(rel).name.lower(), pat.lower()):
                lvl = max(lvl, need)
        return lvl

    def _inside(self, path):
        p = (self.folder / path).resolve()
        if p != self.folder and self.folder not in p.parents:
            raise Refused("that path is outside the hiWR data folder")
        return p

    def list_files(self, pattern="*"):
        if not self.folder.exists():
            return {"folder": str(self.folder), "files": [], "note": "the data folder doesn't exist yet"}
        files = []
        for p in sorted(self.folder.rglob(pattern or "*")):
            if p.is_file():
                rel = p.relative_to(self.folder).as_posix()
                files.append({"path": rel, "bytes": p.stat().st_size,
                              "readable": self.level >= self._file_level(rel)})
        return {"files": files[:500]}

    def read_file(self, path):
        p = self._inside(path)
        rel = p.relative_to(self.folder).as_posix()
        need = self._file_level(rel)
        if self.level < need:
            raise Refused(f"{rel} needs {LEVEL_NAMES[need]} level")
        if not p.is_file():
            raise LookupError(f"no file {rel}")
        if p.suffix.lower() not in {".csv", ".txt", ".md", ".json", ".tsv", ".log"}:
            raise Refused(f"{p.suffix} files can't be read here yet; export it to CSV or text")
        text = p.read_text(encoding="utf-8", errors="replace")
        cut = len(text) > 200_000
        return {"path": rel, "text": privacy.scrub(text[:200_000], self.people), "truncated": cut}

    # ---------- database ----------
    def _authorizer(self, denied):
        def auth(action, a1, a2, dbname, source):
            if action == _SQLITE_SELECT or action == _SQLITE_FUNCTION or action == _SQLITE_RECURSIVE:
                return sqlite3.SQLITE_OK
            if action == _SQLITE_READ:
                if a1 in ("sqlite_master", "sqlite_schema", "sqlite_temp_master"):
                    return sqlite3.SQLITE_OK
                need = TABLE_LEVEL.get(a1, 2)
                if self.level < need:
                    denied.add(f"{a1} (needs {LEVEL_NAMES[need]})")
                    return sqlite3.SQLITE_DENY
                if self.level < COLUMN_LEVEL.get((a1, a2), 0):
                    return sqlite3.SQLITE_IGNORE          # reads as null
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY                    # no writes, pragmas, attach, temp tables
        return auth

    def query_db(self, sql):
        if ";" in sql.strip().rstrip(";"):
            raise Refused("one statement at a time")
        denied = set()
        self.db.set_authorizer(self._authorizer(denied))
        try:
            cur = self.db.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(ROW_LIMIT + 1)
        except sqlite3.DatabaseError as e:
            if denied:
                raise Refused("not available at your level: " + ", ".join(sorted(denied)))
            raise Refused(f"query refused or failed: {e}")
        finally:
            self.db.set_authorizer(None)
        return self._scrub({"columns": cols, "rows": [list(r) for r in rows[:ROW_LIMIT]],
                            "more_rows": len(rows) > ROW_LIMIT})

    # ---------- tickets ----------
    def _fields(self, tid):
        out = {}
        for e in self.db.execute("SELECT detail FROM ticket_events WHERE ticket_id=? AND event IN ('fields','opened_fields') "
                                 "ORDER BY id", (tid,)):
            try:
                out.update(json.loads(e["detail"]))
            except (TypeError, ValueError):
                pass
        return out

    def _money_on(self, tid):
        rows = []
        for a in self.db.execute("SELECT id, scope, amount_cents, description FROM money_actions WHERE ticket_id=?", (tid,)):
            rows.append({"money_action_id": a["id"], "scope": a["scope"], "amount": a["amount_cents"] / 100,
                         "currency": "USD", "status": money_action_status(self.db, a["id"])})
        return rows

    def list_tickets(self, status="open"):
        q, args = "SELECT * FROM tickets", ()
        if status == "open":
            q += " WHERE status != 'closed'"
        elif status:
            q, args = q + " WHERE status=?", (status,)
        out, now = [], self.clock()
        for t in self.db.execute(q + " ORDER BY priority, opened_at", args):
            f = self._fields(t["id"])
            row = {"ticket_id": t["id"], "type": t["type"], "status": t["status"], "priority": t["priority"],
                   "subject": t["subject"], "opened_at": t["opened_at"], "age_days": _age_days(t["opened_at"], now),
                   "next_step": f.get("next_step"), "owner": f.get("owner")}
            if self.level >= 3:
                money = self._money_on(t["id"])
                row["dollars_at_stake"] = sum(m["amount"] for m in money) or (json.loads(t["financial"] or "{}").get("variance_amount"))
                row["approvals"] = [m["status"] for m in money]
            out.append(row)
        return self._scrub({"tickets": out, "financial_fields_shown": self.level >= 3})

    def get_ticket(self, ticket_id):
        t = self._ticket(ticket_id)
        out = {"ticket_id": t["id"], "type": t["type"], "status": t["status"], "priority": t["priority"],
               "subject": t["subject"], "opened_at": t["opened_at"], "fields": self._fields(t["id"]),
               "events": [dict(e) for e in self.db.execute(
                   "SELECT at, actor, event, detail FROM ticket_events WHERE ticket_id=? ORDER BY id", (ticket_id,))]}
        if self.level >= 3:
            out["financial"] = json.loads(t["financial"]) if t["financial"] else None
            out["money_actions"] = self._money_on(ticket_id)
        else:
            out["financial"] = "hidden at this level"
        return self._scrub(out)

    def open_ticket(self, type, subject, dollars=0, perishable=False, party=None, fields=None, financial=None):
        if type not in ("DISPUTE", "SHIPMENT"):
            raise Refused("the assistant opens DISPUTE or SHIPMENT tickets")
        note = None
        if financial and self.level < 3:
            financial, note = None, "financial block not saved: it needs Finance level"
        pr = ticket_priority(self.db, type, dollars=dollars or 0, perishable=bool(perishable), party=party)
        tid = _open_ticket(self.db, type, privacy.scrub(subject, self.people), pr, self.clock(), self._actor(),
                           financial=self._scrub(financial) if financial else None)
        if fields:
            self._event(tid, "opened_fields", json.dumps(self._scrub(fields)))
        return {"ticket_id": tid, "priority": pr, "status": "new", **({"note": note} if note else {})}

    def update_ticket_status(self, ticket_id, status, note=None):
        t = self._ticket(ticket_id)
        if status not in TICKET_STATUSES:
            raise Refused(f"status must be one of {TICKET_STATUSES}")
        if t["status"] == "closed":
            raise Refused(f"{ticket_id} is closed; open a new ticket instead")
        if status == "closed":
            try:
                _close_ticket(self.db, ticket_id, self._actor(), self.clock(), note)
            except PermissionError as e:
                raise Refused(str(e))
            return {"ticket_id": ticket_id, "status": "closed"}
        self.db.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
        self._event(ticket_id, "status", json.dumps({"from": t["status"], "to": status, "note": note}))
        return {"ticket_id": ticket_id, "status": status, "was": t["status"]}

    def add_ticket_note(self, ticket_id, note, fields=None):
        self._ticket(ticket_id)
        self._event(ticket_id, "note", privacy.scrub(note, self.people))
        if fields:
            self._event(ticket_id, "fields", json.dumps(self._scrub(fields)))
        return {"ticket_id": ticket_id, "noted": True}

    # ---------- approvals ----------
    def _approvers(self, scope):
        if scope in MONEY_SCOPES:
            ms = {r[0] for r in self.db.execute("SELECT DISTINCT member FROM approver_certifications")}
            ms |= {r[0] for r in self.db.execute("SELECT DISTINCT member FROM task_grants WHERE task=?", (f"{scope}_approval",))}
            names = sorted(m for m in ms if m != self.requester and is_certified(self.db, m, scope)
                           and member_level(self.db, m) >= 3)
        else:
            names = sorted(r[0] for r in self.db.execute("SELECT member FROM plan_owners") if r[0] != self.requester)
        return names

    def propose_money_action(self, kind, description, amount=None, currency="USD", ticket_id=None):
        if ticket_id:
            self._ticket(ticket_id)
        cents = None
        if amount is not None:
            cents = round(float(amount) * 100)
            if cents < 0:
                raise Refused("amounts are 0 or more; the kind says which way the money moves")
        description = privacy.scrub(description, self.people)
        if kind in MONEY_SCOPES:
            if cents is None:
                raise Refused(f"a {kind} needs an amount (0 for a waiver)")
            mid = request_money_action(self.db, kind, cents, description, self.requester, ticket_id)
            ref, status = f"money action {mid}", money_action_status(self.db, mid)
        elif kind in OTHER_APPROVALS:
            pid = self.db.execute("INSERT INTO agent_proposals(kind, description, amount_cents, currency, ticket_id, "
                                  "requested_by) VALUES (?,?,?,?,?,?)",
                                  (kind, description, cents, currency, ticket_id, self.requester)).lastrowid
            ref, status = f"proposal {pid}", PENDING
        else:
            raise Refused(f"unknown kind {kind}")
        if ticket_id:
            self._event(ticket_id, "proposed", json.dumps({"ref": ref, "kind": kind, "status": status}))
        approvers = self._approvers(kind)
        item = {"ref": ref, "kind": kind, "amount": None if cents is None else cents / 100, "currency": currency,
                "status": status, "approvers": approvers or "none set up yet: the account holder must certify approvers"}
        self.turn_pending.append(item)
        return item

    # ---------- sales and live person ----------
    def verify_sale(self, order_id, card_last4):
        if self.failed_verifications >= 3:
            raise Refused("could not verify; too many tries in this conversation. Offer a live person.")
        last4 = re.sub(r"\D", "", str(card_last4))[-4:]
        r = self.db.execute("SELECT card_last4 FROM order_payments WHERE order_id=? ORDER BY id DESC LIMIT 1",
                            (order_id,)).fetchone()
        if r is None or len(last4) != 4 or r["card_last4"] != last4:
            self.failed_verifications += 1
            return {"verified": False, "message": "could not verify"}
        self.verified_orders.add(int(order_id))
        return {"verified": True, "order_id": int(order_id)}

    def get_order(self, order_id):
        if self.level < 2 and int(order_id) not in self.verified_orders:
            raise Refused("verify the sale first (order # and last 4 of the card)")
        o = self.db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if o is None:
            raise LookupError(f"no order {order_id}")
        out = {"order": dict(o),
               "items": [dict(r) for r in self.db.execute("SELECT item, qty, specs FROM order_items WHERE order_id=?", (order_id,))],
               "stages": [dict(r) for r in self.db.execute("SELECT * FROM order_stage_events WHERE order_id=? ORDER BY id", (order_id,))]}
        if o["appointment_id"]:
            a = self.db.execute("SELECT id, starts_at, ends_at, party_size, kind FROM appointments WHERE id=?",
                                (o["appointment_id"],)).fetchone()
            out["appointment"] = dict(a) if a else None
        if o["shipment_id"]:
            s = self.db.execute("SELECT * FROM shipments WHERE id=?", (o["shipment_id"],)).fetchone()
            out["shipment"] = dict(s) if s else None
        if o["contact_id"]:
            c = self.db.execute("SELECT display_name FROM contacts WHERE id=?", (o["contact_id"],)).fetchone()
            out["client_ref"] = privacy._split(c["display_name"])[0] if c else None
        out["order"].pop("contact_id", None)
        return self._scrub(out)

    def get_business_contacts(self):
        b = self.cfg["business"]
        missing = [k for k in ("phone", "text_number", "hours", "on_duty_manager") if not b.get(k)]
        return {**{k: (b.get(k) or None) for k in ("phone", "text_number", "hours", "on_duty_manager")},
                "missing": missing, "note": "flag missing details for the owner to add; never invent them" if missing else None}

    def request_live_person(self, channel, route_to, reason=None, contact=None, best_time=None, ticket_id=None):
        if ticket_id:
            self._ticket(ticket_id)
        reason = privacy.scrub(reason, self.people) if reason else None
        hid = self.db.execute("INSERT INTO agent_handoffs(requested_for, channel, route_to, reason, contact, best_time, ticket_id) "
                              "VALUES (?,?,?,?,?,?,?)",
                              (self.requester, channel, route_to, reason, contact, best_time, ticket_id)).lastrowid
        if ticket_id:
            self._event(ticket_id, "live_person", json.dumps({"handoff": hid, "channel": channel, "route_to": route_to}))
        return {"handoff_id": hid, "status": "queued - a person must place it", "recorded_on_ticket": ticket_id,
                "business_contacts": self.get_business_contacts()}

    def write_draft(self, filename, content):
        name = re.sub(r"[^\w.-]", "_", Path(filename).name) or "draft.txt"
        d = self.folder / "drafts"
        d.mkdir(parents=True, exist_ok=True)
        p, k = d / name, 1
        while p.exists():                       # never overwrite
            p, k = d / f"{Path(name).stem}-{k}{Path(name).suffix}", k + 1
        p.write_text(privacy.scrub(content, self.people), encoding="utf-8")
        return {"saved": p.relative_to(self.folder).as_posix(), "sent": False}

    # ---------- connected tools ----------
    def quickbooks_read(self, contains=None):
        qb = self.cfg["quickbooks"]
        if not qb.get("connected"):
            return {"connected": False, "note": "QuickBooks is not connected. Reconcile against hiWR data only, "
                                                "and say QuickBooks wasn't checked."}
        p = Path(qb.get("export_file") or "")
        if not p.is_file():
            return {"connected": True, "error": "the QuickBooks export file is missing; ask the owner to export it"}
        with open(p, newline="", encoding="utf-8", errors="replace") as f:
            rows = list(csv.DictReader(f))
        if contains:
            rows = [r for r in rows if contains.lower() in json.dumps(r).lower()]
        return self._scrub({"connected": True, "source": p.name, "read_only": True, "rows": rows[:500],
                            "more_rows": len(rows) > 500})

    def run_script(self, name, args=None):
        p = (SCRIPTS_DIR / f"{name}.py").resolve()
        if p.parent != SCRIPTS_DIR.resolve() or not p.is_file():
            raise Refused(f"{name} is not an approved script")
        if not self.db_path:
            raise Refused("scripts need the database saved to a file")
        args = [str(a) for a in (args or [])]
        r = subprocess.run([sys.executable, "-I", str(p), str(self.db_path), *args], capture_output=True, text=True,
                           timeout=120, cwd=str(self.folder if self.folder.exists() else HERE))
        return {"script": name, "exit_code": r.returncode,
                "output": privacy.scrub(r.stdout[-20000:], self.people), "errors": privacy.scrub(r.stderr[-4000:], self.people)}


def _age_days(opened_at, now):
    try:
        return (datetime.fromisoformat(now[:16]) - datetime.fromisoformat(opened_at[:16])).days
    except ValueError:
        return None
