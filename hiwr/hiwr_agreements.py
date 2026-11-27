"""Business agreements: the forms a small business signs, kept as records HiWR can check work against.

Templates, not legal advice. Every form carries DISCLAIMER and should be reviewed by a North Carolina lawyer
before a business relies on it. The research behind the list of types is in AGREEMENTS.md.

How one agreement moves (nothing is skipped, nothing is edited in place):
  1. Draft. Staff fill the form, or HiWR proposes one (source 'hiwr', e.g. a vendor that delivers every week
     with no supply agreement on file). Each change is a new version with its own hash.
  2. Confirm. A person at the type's sign level reviews the confirm card (Confirm / Fix / Cancel). The checks
     run again on Confirm; a blocking problem stops it.
  3. Money terms. If the form has any dollar term (price, deposit, rent, fee, pay), two different certified
     approvers at Finance level sign off on that exact version: "PENDING - requires double approval".
     Neither can be the person who drafted or confirmed it.
  4. Acceptance. Each party's acceptance (signature, e-signature, emailed reply) is recorded against the
     version hash they saw. A later version needs everything again.
  5. Active. The agreement is then the reference HiWR checks deliveries, invoices and appointments against.
     A mismatch opens or updates a SHIPMENT or DISPUTE ticket with the contract ref in its financial block;
     amounts stay in anticipated costs until a double-approved money action moves them.
"""
import hashlib
import json
from datetime import date

from hiwr_access import level, read, can_see, LEVEL_NAMES
from hiwr_contacts import is_certified, APPROVALS_NEEDED, check_in
from hiwr_processes import open_ticket, ticket_priority

DISCLAIMER = ("Template for review by a North Carolina lawyer before use. Not legal advice. "
              "HiWR checks the form is complete and consistent; it does not decide whether it is enforceable.")

MONEY_PENDING = "PENDING - requires double approval"
ACCEPT_METHODS = ("in_person_signature", "e_signature", "email_reply", "typed_name", "verbal_logged")
FORTY_K = 4_000_000          # $40,000 in cents: NC general contractor license (G.S. 87-1), lien agent (G.S. 44A-11.1)
MIN_WAGE = 725               # $7.25/hour, federal and NC
MIN_TIPPED_CASH = 213        # $2.13/hour cash wage for tipped workers


# ---------------- the catalog ----------------
# field: (name, label, kind, required). kinds: text, cents, int, pct, date, bool, choice:a|b, lines
# 'lines' is a list of dicts; a line with unit_cents (and qty) is a money term.

def F(name, label, kind="text", required=True):
    return {"name": name, "label": label, "kind": kind, "required": required}


TYPES = {
    "vendor_supply": {
        "title": "Vendor supply agreement", "for": ["restaurant", "retail", "construction"],
        "roles": ["business", "vendor"], "sign_level": 2, "money_scope": "contract", "money_view": "vendor_prices",
        "tickets": "SHIPMENT",
        "why": "Standing prices, delivery days and what happens when a delivery is short, damaged or wrong.",
        "fields": [F("items", "Items, unit and agreed price", "lines"), F("delivery_days", "Delivery days"),
                   F("payment_terms_days", "Pay within (days)", "int"), F("food", "Food or drink items", "bool"),
                   F("quality_spec", "Quality spec at receiving (temps, NSF, food code)", required=False),
                   F("shortage_remedy", "If short or damaged", "choice:credit|reship|refund"),
                   F("report_within_hours", "Report problems within (hours)", "int"),
                   F("starts_on", "Starts", "date"), F("ends_on", "Ends", "date", required=False)],
    },
    "purchase_order": {
        "title": "Purchase order", "for": ["restaurant", "retail", "construction", "services"],
        "roles": ["business", "vendor"], "sign_level": 2, "money_scope": "contract", "money_view": "vendor_prices",
        "tickets": "SHIPMENT",
        "why": "One order: what, how many, at what price, delivered by when. Receiving is checked against it.",
        "fields": [F("po_ref", "PO number"), F("items", "Items, quantity and price", "lines"),
                   F("deliver_by", "Deliver by", "date"), F("ship_to", "Ship to"),
                   F("payment_terms_days", "Pay within (days)", "int"),
                   F("shortage_remedy", "If short or damaged", "choice:credit|reship|refund")],
    },
    "client_service": {
        "title": "Client service agreement", "for": ["services", "construction", "retail"],
        "roles": ["business", "client"], "sign_level": 2, "money_scope": "contract", "money_view": "order_costs",
        "tickets": "DISPUTE",
        "why": "Scope, price, deposit and schedule for work done for a client; changes only in writing.",
        "fields": [F("scope_of_work", "Scope of work"), F("price_cents", "Price", "cents"),
                   F("deposit_cents", "Deposit", "cents", required=False), F("schedule", "Schedule"),
                   F("change_orders_in_writing", "Changes only in writing", "bool"),
                   F("cancel_notice_hours", "Cancel notice (hours)", "int"),
                   F("cancel_fee_cents", "Late cancel fee", "cents", required=False),
                   F("warranty_days", "Workmanship warranty (days)", "int", required=False),
                   F("starts_on", "Starts", "date"), F("ends_on", "Ends", "date", required=False)],
    },
    "catering_event": {
        "title": "Catering and event agreement", "for": ["restaurant"],
        "roles": ["business", "client"], "sign_level": 2, "money_scope": "contract", "money_view": "order_costs",
        "tickets": "DISPUTE",
        "why": "Guest count guarantee, menu, allergens, deposit and cancellation for a private event.",
        "fields": [F("event_date", "Event date", "date"), F("guest_count", "Guests", "int"),
                   F("guarantee_due", "Final count due", "date"), F("menu", "Menu"),
                   F("allergens", "Allergies and diets to plan for (write 'none' if none)"),
                   F("price_per_guest_cents", "Price per guest", "cents"), F("deposit_cents", "Deposit", "cents"),
                   F("cancel_fee_cents", "Late cancel fee", "cents", required=False)],
    },
    "appointment_terms": {
        "title": "Appointment and late fee terms", "for": ["services", "restaurant"],
        "roles": ["business", "client"], "sign_level": 2, "money_scope": "fee", "money_view": "order_costs",
        "tickets": "DISPUTE",
        "why": "What a client agrees to before booking: late and no-show fees, cancel notice, reminders.",
        "fields": [F("late_minutes", "Late after (minutes)", "int"), F("late_fee_cents", "Late fee", "cents"),
                   F("no_show_fee_cents", "No-show fee", "cents"), F("cancel_notice_hours", "Cancel notice (hours)", "int"),
                   F("reminder_business_days", "Reminder sent (business days before)", "int"),
                   F("client_consented", "Client saw and agreed to the fees before booking", "bool")],
    },
    "offer_letter": {
        "title": "Job offer and wage notice", "for": ["restaurant", "retail", "construction", "services"],
        "roles": ["business", "worker"], "sign_level": 2, "money_scope": "payroll", "money_view": "payroll",
        "tickets": None,
        "why": "NC law (G.S. 95-25.13) requires telling a new hire in writing the promised wages and the day and "
               "place they are paid.",
        "fields": [F("role", "Role"), F("pay_cents", "Pay", "cents"), F("pay_basis", "Pay basis", "choice:hourly|yearly"),
                   F("tipped", "Tipped role", "bool"), F("pay_day", "Pay day"), F("pay_place", "How and where paid"),
                   F("start_on", "Start date", "date"), F("schedule", "Expected schedule"),
                   F("at_will", "At-will employment", "bool")],
    },
    "independent_contractor": {
        "title": "Independent contractor agreement", "for": ["services", "construction", "retail", "restaurant"],
        "roles": ["business", "contractor"], "sign_level": 2, "money_scope": "contract", "money_view": "payroll",
        "tickets": "DISPUTE",
        "why": "Work bought from a 1099 contractor: deliverables, rate, insurance. Misclassification is the risk.",
        "fields": [F("scope_of_work", "Scope of work"), F("rate_cents", "Rate", "cents"),
                   F("rate_basis", "Rate basis", "choice:hourly|fixed|per_unit"), F("deliverables", "Deliverables"),
                   F("contractor_sets_hours", "Contractor sets own hours", "bool"),
                   F("contractor_own_tools", "Contractor uses own tools", "bool"),
                   F("insurance_on_file", "Certificate of insurance on file", "bool"),
                   F("starts_on", "Starts", "date"), F("ends_on", "Ends", "date", required=False)],
    },
    "subcontract": {
        "title": "Subcontractor agreement", "for": ["construction"],
        "roles": ["business", "subcontractor"], "sign_level": 4, "money_scope": "subcontract", "money_view": "payroll",
        "tickets": "DISPUTE",
        "why": "Lab jobs: scope, contract sum, draws by phase, retainage, lien waivers, insurance and license.",
        "fields": [F("job_ref", "Job"), F("scope_of_work", "Scope of work"), F("contract_sum_cents", "Contract sum", "cents"),
                   F("retainage_pct", "Retainage %", "pct"), F("draws", "Draws by phase (phase, pct)", "lines"),
                   F("lien_waiver_each_draw", "Lien waiver with each draw", "bool"),
                   F("insurance_on_file", "Certificate of insurance on file", "bool"),
                   F("license_number", "NC contractor license number", required=False),
                   F("starts_on", "Starts", "date"), F("ends_on", "Finish by", "date")],
    },
    "construction_client": {
        "title": "Construction agreement with the owner", "for": ["construction"],
        "roles": ["business", "owner"], "sign_level": 4, "money_scope": "contract", "money_view": "order_costs",
        "tickets": "DISPUTE",
        "why": "House builds and remodels: contract sum, draws, change orders, lien agent, warranty.",
        "fields": [F("job_ref", "Job"), F("site", "Site"), F("contract_sum_cents", "Contract sum", "cents"),
                   F("deposit_cents", "Deposit", "cents", required=False), F("draws", "Draws by phase (phase, pct)", "lines"),
                   F("change_orders_in_writing", "Changes only by signed change order", "bool"),
                   F("owner_occupied_existing_home", "Existing single-family home the owner lives in", "bool"),
                   F("lien_agent", "Mechanics lien agent", required=False),
                   F("license_number", "NC general contractor license number", required=False),
                   F("warranty_months", "Warranty (months)", "int"),
                   F("starts_on", "Starts", "date"), F("ends_on", "Finish by", "date")],
    },
    "nda": {
        "title": "Confidentiality agreement (NDA)", "for": ["restaurant", "retail", "construction", "services"],
        "roles": ["business", "other_party"], "sign_level": 2, "money_scope": None, "money_view": None,
        "tickets": "DISPUTE",
        "why": "Recipes, pricing, client lists, plans shared with a partner, investor or key employee.",
        "fields": [F("mutual", "Both sides share secrets", "bool"), F("what", "What is confidential"),
                   F("purpose", "Why it is being shared"), F("years", "How long (years)", "int"),
                   F("starts_on", "Starts", "date")],
    },
    "non_compete": {
        "title": "Non-compete / non-solicit", "for": ["services", "retail", "construction", "restaurant"],
        "roles": ["business", "worker"], "sign_level": 4, "money_scope": "payroll", "money_view": "payroll",
        "tickets": None,
        "why": "NC enforces these only in writing, as part of the employment deal, for real consideration, and "
               "reasonable in time and territory.",
        "fields": [F("role", "Role"), F("months", "Length after leaving (months)", "int"),
                   F("territory", "Territory"), F("restricted_work", "Work it restricts"),
                   F("consideration", "What the worker gets for it", "choice:new_job|raise_or_bonus|none"),
                   F("bonus_cents", "Raise or bonus paid for it", "cents", required=False),
                   F("starts_on", "Signed with start or change on", "date")],
    },
    "commercial_lease": {
        "title": "Commercial lease", "for": ["restaurant", "retail", "services"],
        "roles": ["business", "landlord"], "sign_level": 4, "money_scope": "contract", "money_view": "order_costs",
        "tickets": "DISPUTE",
        "why": "Premises, rent, term, shared costs (CAM), deposit, repairs, renewal, personal guarantee.",
        "fields": [F("premises", "Premises"), F("rent_cents", "Monthly rent", "cents"),
                   F("cam_cents", "Monthly shared costs (CAM)", "cents", required=False),
                   F("deposit_cents", "Security deposit", "cents"), F("term_months", "Term (months)", "int"),
                   F("repairs", "Who repairs what"), F("renewal", "Renewal option", required=False),
                   F("personal_guarantee", "Owner personally guarantees", "bool"),
                   F("starts_on", "Starts", "date"), F("ends_on", "Ends", "date")],
    },
    "equipment_rental": {
        "title": "Equipment lease or rental", "for": ["restaurant", "construction", "retail"],
        "roles": ["business", "lessor"], "sign_level": 2, "money_scope": "contract", "money_view": "vendor_prices",
        "tickets": "DISPUTE",
        "why": "Ice machines, dish machines, lifts, POS terminals: rate, term, service, damage.",
        "fields": [F("equipment", "Equipment"), F("rate_cents", "Rate", "cents"),
                   F("rate_basis", "Rate basis", "choice:daily|weekly|monthly"),
                   F("deposit_cents", "Deposit", "cents", required=False), F("service", "Who services it"),
                   F("damage", "Damage and loss terms"), F("starts_on", "Starts", "date"), F("ends_on", "Ends", "date")],
    },
    "warranty": {
        "title": "Warranty terms", "for": ["retail", "construction", "services"],
        "roles": ["business", "client"], "sign_level": 2, "money_scope": None, "money_view": None,
        "tickets": "DISPUTE",
        "why": "What the business stands behind, for how long, and how a claim is made. Claims open a DISPUTE.",
        "fields": [F("covers", "What it covers"), F("months", "How long (months)", "int"),
                   F("excludes", "What it does not cover"), F("claim_how", "How to make a claim"),
                   F("remedy", "Remedy", "choice:repair|replace|refund"), F("starts_on", "Starts", "date")],
    },
}


# ---------------- validation and checks ----------------

def _valid_value(kind, v):
    if kind in ("cents", "int"):
        return isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if kind == "pct":
        return isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 100
    if kind == "bool":
        return isinstance(v, bool)
    if kind == "date":
        try:
            date.fromisoformat(v)
            return True
        except (TypeError, ValueError):
            return False
    if kind.startswith("choice:"):
        return v in kind[7:].split("|")
    if kind == "lines":
        return isinstance(v, list) and len(v) > 0 and all(isinstance(x, dict) for x in v)
    return isinstance(v, str) and v.strip() != ""


def problems(type_key, fields):
    """[(blocking, message)]. Blocking problems stop Confirm; the others are shown on the card."""
    t = TYPES[type_key]
    out = []
    parties = fields.get("parties") or {}
    for role in t["roles"]:
        if not str(parties.get(role, "")).strip():
            out.append((True, f"Name the {role.replace('_', ' ')}"))
    known = {f["name"]: f for f in t["fields"]}
    for name in fields:
        if name not in known and name != "parties":
            out.append((True, f"'{name}' is not a field on this form"))
    for f in t["fields"]:
        v = fields.get(f["name"])
        if v is None:
            if f["required"]:
                out.append((True, f"{f['label']} is missing"))
        elif not _valid_value(f["kind"], v):
            out.append((True, f"{f['label']} doesn't look right ({v!r})"))
    if any(b for b, _ in out):
        return out                       # rules below assume the fields are well formed
    g = fields.get
    if g("starts_on") and g("ends_on") and g("ends_on") < g("starts_on"):
        out.append((True, "Ends before it starts"))
    for line in g("items") or []:
        if "unit_cents" in line and not _valid_value("cents", line["unit_cents"]):
            out.append((True, f"Price for {line.get('name', 'an item')} must be whole cents, 0 or more"))
        if "qty" in line and not _valid_value("int", line["qty"]):
            out.append((True, f"Quantity for {line.get('name', 'an item')} must be a whole number"))
    if g("draws") is not None:
        total = sum(d.get("pct", 0) for d in g("draws"))
        if total != 100:
            out.append((True, f"Draws add up to {total}%, not 100%"))

    if type_key == "vendor_supply" and g("food") and not g("quality_spec"):
        out.append((True, "Food deliveries need a quality spec checked at receiving (temps, NSF, food code)"))
    if type_key == "offer_letter":
        if g("pay_basis") == "hourly":
            floor, what = (MIN_TIPPED_CASH, "$2.13 cash wage for tipped work") if g("tipped") else (MIN_WAGE, "$7.25 minimum wage")
            if g("pay_cents") < floor:
                out.append((True, f"Pay is below the {what}"))
        out.append((False, "Give this to the worker in writing at hiring, and give written notice at least one pay "
                           "period before any cut in pay (NC G.S. 95-25.13)"))
    if type_key == "independent_contractor" and (not g("contractor_sets_hours") or not g("contractor_own_tools")):
        out.append((False, "The business sets the hours or supplies the tools: this may be an employee, not a "
                           "contractor. Check with an accountant or lawyer"))
    if type_key in ("subcontract", "construction_client"):
        if g("contract_sum_cents") >= FORTY_K and not g("license_number"):
            out.append((True, "Work of $40,000 or more needs an NC general contractor license number (G.S. 87-1)"))
        if type_key == "subcontract" and not g("lien_waiver_each_draw"):
            out.append((False, "No lien waiver with each draw: the owner may be exposed to the sub's suppliers"))
        if type_key == "subcontract" and not g("insurance_on_file"):
            out.append((False, "No certificate of insurance on file"))
    if type_key == "construction_client" and g("contract_sum_cents") >= FORTY_K \
            and not g("owner_occupied_existing_home") and not g("lien_agent"):
        out.append((True, "Improvements of $40,000 or more need a lien agent named before the first contract "
                          "(G.S. 44A-11.1), unless it is an existing home the owner lives in"))
    if type_key == "non_compete":
        if g("consideration") == "none":
            out.append((True, "A non-compete needs something in return: a new job, or for a current worker a raise "
                              "or bonus"))
        if g("consideration") == "raise_or_bonus" and not g("bonus_cents"):
            out.append((True, "Say what raise or bonus is paid for it"))
        if g("months") >= 60:
            out.append((True, "Five years or more is presumed unreasonable in NC"))
        elif g("months") > 24:
            out.append((False, "NC courts have generally approved up to 2 years; longer is at risk"))
    if type_key == "appointment_terms":
        if not g("client_consented"):
            out.append((True, "The client has to see and agree to the fees before booking"))
        if g("reminder_business_days") < 1:
            out.append((True, "Send the reminder at least 1 business day before"))
    if type_key == "catering_event" and g("guarantee_due") >= g("event_date"):
        out.append((True, "The final count has to be due before the event"))
    if type_key == "commercial_lease" and g("personal_guarantee"):
        out.append((False, "Personal guarantee: the owner's own money is on the line if the business can't pay"))
    return out


def money_terms(type_key, fields):
    """[(label, cents)] for every dollar term on the form, plus a line total for priced items."""
    t = TYPES[type_key]
    out = []
    for f in t["fields"]:
        v = fields.get(f["name"])
        if f["kind"] == "cents" and v is not None:
            out.append((f["label"], v))
        if f["kind"] == "lines" and v:
            priced = [x for x in v if "unit_cents" in x]
            if priced and all("qty" in x for x in priced):
                out.append((f"{f['label']} (total)", sum(x["unit_cents"] * x["qty"] for x in priced)))
            else:
                out += [(f"{x.get('name', 'item')} per {x.get('unit', 'unit')}", x["unit_cents"]) for x in priced]
    return out


# ---------------- records ----------------

def _hash(type_key, fields):
    return hashlib.sha256(json.dumps({"type": type_key, "fields": fields}, sort_keys=True).encode()).hexdigest()


def _agreement(db, aid):
    a = db.execute("SELECT * FROM agreements WHERE id=?", (aid,)).fetchone()
    if a is None:
        raise LookupError(f"no agreement {aid}")
    return a


def latest(db, aid):
    v = db.execute("SELECT * FROM agreement_versions WHERE agreement_id=? ORDER BY version DESC LIMIT 1",
                   (aid,)).fetchone()
    return {"version": v["version"], "hash": v["hash"], "fields": json.loads(v["fields"]), "made_by": v["made_by"]}


def _events(db, aid, version=None, event=None):
    q, args = "SELECT * FROM agreement_events WHERE agreement_id=?", [aid]
    if version is not None:
        q += " AND version=?"; args.append(version)
    if event is not None:
        q += " AND event=?"; args.append(event)
    return db.execute(q + " ORDER BY id", args).fetchall()


def _event(db, aid, v, event, actor, now, party_role=None, method=None, note=None):
    db.execute("INSERT INTO agreement_events(agreement_id, version, hash, event, actor, party_role, method, note, at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (aid, v["version"], v["hash"], event, actor, party_role, method, note, now))


def draft(db, type_key, fields, drafted_by, now, title=None):
    """Staff fill a form. It can be incomplete: the card says what is missing."""
    if type_key not in TYPES:
        raise ValueError(f"unknown agreement type {type_key!r}")
    if drafted_by == "hiwr":
        raise ValueError("use propose() for HiWR's own drafts")
    return _create(db, type_key, fields, drafted_by, "staff", now, title)


def propose(db, type_key, fields, now, reason, title=None):
    """HiWR proposes a draft (it never confirms, approves or accepts one)."""
    if type_key not in TYPES:
        raise ValueError(f"unknown agreement type {type_key!r}")
    return _create(db, type_key, fields, "hiwr", "hiwr", now, title, reason)


def _create(db, type_key, fields, by, source, now, title, reason=None):
    title = title or TYPES[type_key]["title"]
    aid = db.execute("INSERT INTO agreements(type, title, drafted_by, source, created_at) VALUES (?,?,?,?,?)",
                     (type_key, title, by, source, now)).lastrowid
    db.execute("INSERT INTO agreement_versions(agreement_id, version, fields, hash, made_by, reason, at) "
               "VALUES (?,?,?,?,?,?,?)", (aid, 1, json.dumps(fields, sort_keys=True), _hash(type_key, fields),
                                          by, reason, now))
    return aid


def revise(db, aid, changes, by, now, reason=None):
    """A change is a new version. Confirmations, approvals and signatures on older versions stop counting."""
    a = _agreement(db, aid)
    if status(db, aid) in ("cancelled", "ended"):
        raise PermissionError(f"agreement {aid} is {status(db, aid)}; start a new one")
    cur = latest(db, aid)
    fields = {**cur["fields"], **changes}
    fields = {k: v for k, v in fields.items() if v is not None}
    db.execute("INSERT INTO agreement_versions(agreement_id, version, fields, hash, made_by, reason, at) "
               "VALUES (?,?,?,?,?,?,?)", (aid, cur["version"] + 1, json.dumps(fields, sort_keys=True),
                                          _hash(a["type"], fields), by, reason, now))
    return cur["version"] + 1


def _person(db, member, what):
    if not member or member == "hiwr":
        raise PermissionError(f"HiWR proposes; a person has to {what}")


def confirm(db, aid, member, now, seen_hash=None):
    """Confirm on the card. Checked again here, so a stale card or a changed form can't slip through."""
    _person(db, member, "confirm")
    a, v = _agreement(db, aid), latest(db, aid)
    t = TYPES[a["type"]]
    if status(db, aid) in ("cancelled", "ended"):
        raise PermissionError(f"agreement {aid} is {status(db, aid)}")
    if seen_hash and seen_hash != v["hash"]:
        raise PermissionError("the form changed since this card was shown; review the new version")
    if level(db, member) < t["sign_level"]:
        raise PermissionError(f"{t['title']} is confirmed at {LEVEL_NAMES[t['sign_level']]} level or higher")
    blocking = [m for b, m in problems(a["type"], v["fields"]) if b]
    if blocking:
        raise ValueError("can't confirm yet: " + "; ".join(blocking))
    if _events(db, aid, v["version"], "confirmed"):
        raise ValueError(f"version {v['version']} is already confirmed")
    _event(db, aid, v, "confirmed", member, now)
    return status(db, aid)


def request_fix(db, aid, member, now, note):
    _person(db, member, "ask for a fix")
    _event(db, aid, latest(db, aid), "fix_requested", member, now, note=note)


def cancel(db, aid, member, now, note=None):
    _person(db, member, "cancel")
    a = _agreement(db, aid)
    if level(db, member) < TYPES[a["type"]]["sign_level"]:
        raise PermissionError("cancelling takes the same level as confirming")
    _event(db, aid, latest(db, aid), "cancelled", member, now, note=note)


def money_decision(db, aid, approver, now, decision="approve", note=None):
    """Two different certified Finance-level approvers sign off on the money terms of this exact version.
    Not the drafter, not whoever made this version, not whoever confirmed it."""
    _person(db, approver, "approve money")
    a, v = _agreement(db, aid), latest(db, aid)
    scope = TYPES[a["type"]]["money_scope"]
    if decision not in ("approve", "reject"):
        raise ValueError("decision is approve or reject")
    if not money_terms(a["type"], v["fields"]) or scope is None:
        raise ValueError("this agreement has no money terms to approve")
    if not _events(db, aid, v["version"], "confirmed"):
        raise PermissionError("confirm the form before its money terms are approved")
    if not is_certified(db, approver, scope):
        raise PermissionError(f"{approver} is not certified for {scope} approvals")
    involved = {a["drafted_by"], v["made_by"]} | {e["actor"] for e in _events(db, aid, v["version"], "confirmed")}
    if approver in involved:
        raise PermissionError("the person who drafted, changed or confirmed this agreement cannot approve its money")
    ms = money_status(db, aid)
    if ms != "none" and not ms.startswith(MONEY_PENDING):
        raise PermissionError(f"money terms are already {ms}")
    if any(e["actor"] == approver for e in _events(db, aid, v["version"])
           if e["event"] in ("money_approve", "money_reject")):
        raise PermissionError(f"{approver} already decided; the second approval has to come from someone else")
    _event(db, aid, v, "money_approve" if decision == "approve" else "money_reject", approver, now, note=note)
    return money_status(db, aid)


def money_status(db, aid):
    a, v = _agreement(db, aid), latest(db, aid)
    if not money_terms(a["type"], v["fields"]) or TYPES[a["type"]]["money_scope"] is None:
        return "none"
    evs = [e for e in _events(db, aid, v["version"]) if e["event"] in ("money_approve", "money_reject")]
    if any(e["event"] == "money_reject" for e in evs):
        return "REJECTED"
    n = len({e["actor"] for e in evs})
    return "APPROVED" if n >= APPROVALS_NEEDED else f"{MONEY_PENDING} ({n}/{APPROVALS_NEEDED})"


def accept(db, aid, party_role, recorded_by, method, now, seen_hash, note=None):
    """Record one party's acceptance of the exact version they saw (seen_hash). The business's own signature
    has to be recorded by someone at the type's sign level."""
    _person(db, recorded_by, "record a signature")
    a, v = _agreement(db, aid), latest(db, aid)
    t = TYPES[a["type"]]
    if party_role not in t["roles"]:
        raise ValueError(f"{party_role} is not a party to a {t['title']}")
    if method not in ACCEPT_METHODS:
        raise ValueError(f"method is one of {', '.join(ACCEPT_METHODS)}")
    if seen_hash != v["hash"]:
        raise PermissionError("that signature is for a different version of the form; show them the current one")
    if not _events(db, aid, v["version"], "confirmed"):
        raise PermissionError("confirm the form before anyone signs it")
    if money_status(db, aid) not in ("none", "APPROVED"):
        raise PermissionError(f"money terms are {money_status(db, aid)}; nobody signs until they are approved")
    if party_role == "business" and level(db, recorded_by) < t["sign_level"]:
        raise PermissionError(f"the business signs at {LEVEL_NAMES[t['sign_level']]} level or higher")
    if any(e["party_role"] == party_role for e in _events(db, aid, v["version"], "accepted")):
        raise ValueError(f"{party_role} already accepted version {v['version']}")
    _event(db, aid, v, "accepted", recorded_by, now, party_role=party_role, method=method, note=note)
    st = status(db, aid)
    if st == "active":
        check_in(db, f"agreement:{aid}", recorded_by, True, True, "all parties accepted")
    return st


def end(db, aid, member, now, note):
    _person(db, member, "end an agreement")
    if level(db, member) < TYPES[_agreement(db, aid)["type"]]["sign_level"]:
        raise PermissionError("ending takes the same level as confirming")
    _event(db, aid, latest(db, aid), "ended", member, now, note=note)


def status(db, aid):
    a, v = _agreement(db, aid), latest(db, aid)
    every = _events(db, aid)
    if any(e["event"] == "cancelled" for e in every):
        return "cancelled"
    if any(e["event"] == "ended" for e in every):
        return "ended"
    if not _events(db, aid, v["version"], "confirmed"):
        if any(b for b, _ in problems(a["type"], v["fields"])):
            return "draft - needs fixes"
        return "draft - awaiting confirmation"
    ms = money_status(db, aid)
    if ms not in ("none", "APPROVED"):
        return f"money terms {ms}" if ms == "REJECTED" else ms
    signed = {e["party_role"] for e in _events(db, aid, v["version"], "accepted")}
    waiting = [r for r in TYPES[a["type"]]["roles"] if r not in signed]
    if waiting:
        return "awaiting acceptance: " + ", ".join(waiting)
    return "active"


# ---------------- cards and views ----------------

def usd(c):
    return f"${c / 100:,.2f}"


def _can_see_money(db, member, type_key):
    need = TYPES[type_key]["money_view"]
    return need is None or can_see(db, member, need)


def confirm_card(db, aid, member):
    """What the person sees before Confirm / Fix / Cancel."""
    a, v = _agreement(db, aid), latest(db, aid)
    probs = problems(a["type"], v["fields"])
    card = {"agreement": aid, "title": a["title"], "type": TYPES[a["type"]]["title"], "version": v["version"],
            "hash": v["hash"], "proposed_by": "HiWR" if a["source"] == "hiwr" else a["drafted_by"],
            "parties": v["fields"].get("parties", {}), "status": status(db, aid),
            "must_fix": [m for b, m in probs if b], "notes": [m for b, m in probs if not b],
            "disclaimer": DISCLAIMER, "actions": ["Confirm", "Fix", "Cancel"]}
    terms = money_terms(a["type"], v["fields"])
    if terms:
        need = TYPES[a["type"]]["money_view"]
        card["money"] = read(db, member, need, lambda: [(l, usd(c)) for l, c in terms],
                             lambda: "hidden at your level")
        card["money_rule"] = f"{MONEY_PENDING}: two certified {TYPES[a['type']]['money_scope']} approvers"
    return card


def render(db, aid, member, business="the Business"):
    """The agreement as text, money terms hidden from anyone below the level that may see them."""
    a, v = _agreement(db, aid), latest(db, aid)
    t, f = TYPES[a["type"]], v["fields"]
    show_money = _can_see_money(db, member, a["type"])
    parties = f.get("parties", {})
    lines = [f"# {a['title']}", "", f"_{DISCLAIMER}_", "",
             f"Version {v['version']} ({v['hash'][:12]}). Status: {status(db, aid)}.", "", "## Parties", ""]
    for role in t["roles"]:
        lines.append(f"- {role.replace('_', ' ').capitalize()}: {parties.get(role, '(missing)')}")
    lines += ["", "## Terms", ""]
    for fd in t["fields"]:
        val = f.get(fd["name"])
        if val is None:
            continue
        if fd["kind"] == "cents":
            val = usd(val) if show_money else "(hidden at your level)"
        elif fd["kind"] == "bool":
            val = "yes" if val else "no"
        elif fd["kind"] == "pct":
            val = f"{val}%"
        elif fd["kind"] == "lines":
            parts = []
            for x in val:
                bits = [str(x.get("name") or x.get("phase", ""))]
                if "qty" in x: bits.append(f"x{x['qty']}")
                if "unit" in x: bits.append(x["unit"])
                if "pct" in x: bits.append(f"{x['pct']}%")
                if "unit_cents" in x: bits.append(f"at {usd(x['unit_cents'])}" if show_money else "at (hidden)")
                parts.append(" ".join(bits))
            val = "; ".join(parts)
        lines.append(f"- {fd['label']}: {val}")
    notes = [m for b, m in problems(a["type"], f) if not b]
    if notes:
        lines += ["", "## HiWR notes", ""] + [f"- {m}" for m in notes]
    lines += ["", "## Sign-off", ""]
    for e in _events(db, aid, v["version"]):
        if e["event"] == "confirmed":
            lines.append(f"- Confirmed by {e['actor']} {e['at']}")
        elif e["event"] in ("money_approve", "money_reject"):
            lines.append(f"- Money terms {'approved' if e['event'] == 'money_approve' else 'rejected'} by {e['actor']} {e['at']}")
        elif e["event"] == "accepted":
            lines.append(f"- {e['party_role'].replace('_', ' ').capitalize()} accepted ({e['method'].replace('_', ' ')}) "
                         f"{e['at']}, recorded by {e['actor']}")
    ms = money_status(db, aid)
    if ms.startswith(MONEY_PENDING):
        lines.append(f"- Money terms: {ms}")
    return "\n".join(lines) + "\n"


def register(db, member, today):
    """Every agreement with its status, who it's with and when it ends; money hidden by level."""
    out = []
    for a in db.execute("SELECT * FROM agreements ORDER BY id"):
        v = latest(db, a["id"])
        row = {"id": a["id"], "type": a["type"], "title": a["title"], "status": status(db, a["id"]),
               "with": {k: p for k, p in v["fields"].get("parties", {}).items() if k != "business"},
               "ends_on": v["fields"].get("ends_on")}
        if _can_see_money(db, member, a["type"]):
            row["money"] = [(l, usd(c)) for l, c in money_terms(a["type"], v["fields"])]
        out.append(row)
    return out


def renewals_due(db, today, days=30):
    """Active agreements that end within `days`: renew, renegotiate or let lapse is a person's call."""
    d0 = date.fromisoformat(today)
    out = []
    for a in db.execute("SELECT id, title FROM agreements ORDER BY id"):
        ends = latest(db, a["id"])["fields"].get("ends_on")
        if ends and status(db, a["id"]) == "active" and 0 <= (date.fromisoformat(ends) - d0).days <= days:
            out.append({"id": a["id"], "title": a["title"], "ends_on": ends,
                        "days_left": (date.fromisoformat(ends) - d0).days})
    return out


# ---------------- links: invoices, POs, tickets ----------------

def link(db, aid, kind, ref, by, now):
    _agreement(db, aid)
    db.execute("INSERT OR IGNORE INTO agreement_links(agreement_id, kind, ref, linked_by, at) VALUES (?,?,?,?,?)",
               (aid, kind, str(ref), by, now))


def links(db, aid, kind=None):
    q, args = "SELECT kind, ref FROM agreement_links WHERE agreement_id=?", [aid]
    if kind:
        q += " AND kind=?"; args.append(kind)
    return [(r["kind"], r["ref"]) for r in db.execute(q + " ORDER BY id", args)]


def find(db, kind, ref):
    """The agreement an invoice, PO or ticket belongs to (None if it has none)."""
    r = db.execute("SELECT agreement_id FROM agreement_links WHERE kind=? AND ref=? ORDER BY id LIMIT 1",
                   (kind, str(ref))).fetchone()
    return r["agreement_id"] if r else None


def _contract_ref(db, aid):
    v = latest(db, aid)
    return f"AGR-{aid:04d} v{v['version']} ({v['hash'][:12]})"


def check_delivery(db, aid, received, now, actor, invoice_ref=None, perishable=False):
    """Compare a delivery and its invoice with the agreed items and prices.
    received: [{"name", "qty_expected", "qty", "unit_cents"}] (unit_cents = price on the invoice).
    A variance opens a SHIPMENT ticket (or adds to the open one for the same invoice) whose financial block
    carries the contract ref, expected vs received, the variance and a proposed resolution that stays in
    anticipated costs until two approvers move it. Returns the ticket id, or None when everything matches."""
    a, v = _agreement(db, aid), latest(db, aid)
    if a["type"] not in ("vendor_supply", "purchase_order"):
        raise ValueError("deliveries are checked against a vendor supply agreement or a purchase order")
    if status(db, aid) != "active":
        raise PermissionError(f"agreement {aid} is {status(db, aid)}, not active; nothing to check against yet")
    agreed = {x["name"]: x for x in v["fields"]["items"]}
    rows, variance = [], 0
    for r in received:
        ag = agreed.get(r["name"])
        if ag is None:
            rows.append({"item": r["name"], "problem": "not on the agreement",
                         "variance_cents": r["qty"] * r["unit_cents"]})
            variance += r["qty"] * r["unit_cents"]
            continue
        price = ag["unit_cents"]
        short = max(r["qty_expected"] - r["qty"], 0)
        over_price = max(r["unit_cents"] - price, 0)
        if short or over_price:
            cents = short * price + r["qty"] * over_price
            rows.append({"item": r["name"], "expected_qty": r["qty_expected"], "received_qty": r["qty"],
                         "agreed_unit_cents": price, "invoiced_unit_cents": r["unit_cents"], "variance_cents": cents})
            variance += cents
    if not rows:
        return None
    remedy = v["fields"].get("shortage_remedy", "credit")
    vendor = v["fields"]["parties"]["vendor"]
    financial = {"contract_ref": _contract_ref(db, aid), "invoice_ref": invoice_ref,
                 "po_ref": v["fields"].get("po_ref"), "lines": rows, "variance_cents": variance,
                 "proposed_resolution": f"ask {vendor} for a {remedy} of {usd(variance)}",
                 "lands_in": "anticipated costs", "money": f"{MONEY_PENDING} before any credit or refund is posted"}
    open_tid = None
    for kind, ref in links(db, aid, "ticket"):
        t = db.execute("SELECT * FROM tickets WHERE id=?", (ref,)).fetchone()
        if t and t["status"] != "closed" and t["type"] == "SHIPMENT" and invoice_ref and \
                json.loads(t["financial"] or "{}").get("invoice_ref") == invoice_ref:
            open_tid = ref
    if open_tid:
        db.execute("INSERT INTO ticket_events(ticket_id, at, actor, event, detail) VALUES (?,?,?,?,?)",
                   (open_tid, now, actor, "updated", json.dumps(financial, sort_keys=True)))
        return open_tid
    subject = f"{vendor}: delivery off agreement {_contract_ref(db, aid).split(' ')[0]}"
    pr = ticket_priority(db, "SHIPMENT", dollars=variance / 100, perishable=perishable, party=vendor)
    tid = open_ticket(db, "SHIPMENT", subject, pr, now, actor, financial)
    link(db, aid, "ticket", tid, actor, now)
    if invoice_ref:
        link(db, aid, "invoice", invoice_ref, actor, now)
    return tid


def open_dispute(db, aid, subject, amount_cents, now, actor, invoice_ref=None, proposed_resolution=None):
    """A client or partner disputes something an agreement covers: DISPUTE ticket with the contract ref."""
    a, v = _agreement(db, aid), latest(db, aid)
    if not isinstance(amount_cents, int) or isinstance(amount_cents, bool) or amount_cents < 0:
        raise ValueError("amount is whole cents, 0 or more")
    other = [p for r, p in v["fields"].get("parties", {}).items() if r != "business"]
    financial = {"contract_ref": _contract_ref(db, aid), "invoice_ref": invoice_ref,
                 "disputed_cents": amount_cents, "agreed_terms": dict(money_terms(a["type"], v["fields"])),
                 "proposed_resolution": proposed_resolution, "lands_in": "anticipated costs",
                 "money": f"{MONEY_PENDING} before any refund, credit or fee"}
    pr = ticket_priority(db, "DISPUTE", dollars=amount_cents / 100, party=other[0] if other else None)
    tid = open_ticket(db, "DISPUTE", f"{subject} ({_contract_ref(db, aid).split(' ')[0]})", pr, now, actor, financial)
    link(db, aid, "ticket", tid, actor, now)
    if invoice_ref:
        link(db, aid, "invoice", invoice_ref, actor, now)
    return tid


# ---------------- HiWR proposals ----------------

def suggest_missing(db, min_deliveries=3):
    """Vendors that keep delivering with no supply agreement on file. HiWR suggests; a person decides."""
    covered = set()
    for a in db.execute("SELECT id FROM agreements WHERE type IN ('vendor_supply', 'purchase_order')"):
        if status(db, a["id"]) not in ("cancelled", "ended"):
            covered.add(latest(db, a["id"])["fields"].get("parties", {}).get("vendor"))
    out = []
    for r in db.execute("SELECT c.display_name AS vendor, COUNT(*) AS n FROM shipments s JOIN contacts c "
                        "ON c.id = s.contact_id WHERE s.direction='inbound' GROUP BY c.id HAVING COUNT(*) >= ? "
                        "ORDER BY n DESC", (min_deliveries,)):
        if r["vendor"] not in covered:
            out.append({"vendor": r["vendor"], "deliveries": r["n"], "suggest": "vendor_supply"})
    return out
