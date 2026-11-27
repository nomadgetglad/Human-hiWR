"""Run: python agreements_demo.py
Business agreements end to end on sample data (a Wilmington restaurant, sample names and prices):
HiWR proposes a vendor supply agreement, a person confirms, two approvers sign off the money terms, both
parties accept, and a short, overpriced delivery opens a SHIPMENT ticket against the agreement."""
import json

from hiwr_contacts import connect, add_plan_owner, certify, add_contact
from hiwr_access import set_level
import hiwr_agreements as ag

NOW = "2026-12-01T09:00"
db = connect()
add_plan_owner(db, "owner_alex", "setup")
for m in ("manager_jo", "accounting_sue"):
    set_level(db, m, 3, "owner_alex")
    for scope in ("contract", "payroll", "fee", "subcontract"):
        certify(db, m, scope, "owner_alex")
set_level(db, "chef_marco", 2, "owner_alex")
set_level(db, "cook_luis", 0, "owner_alex")

print("== Agreement forms in HiWR (sample) ==")
for key, t in ag.TYPES.items():
    print(f"  {t['title']:38} confirm at {ag.LEVEL_NAMES[t['sign_level']]:15} money: {t['money_scope'] or '-'}")

# A vendor that keeps delivering with nothing on file
vendor = add_contact(db, "Coastal Produce", "owner_alex", kind="vendor")["id"]
for d in ("2026-11-03", "2026-11-10", "2026-11-17", "2026-11-24"):
    db.execute("INSERT INTO shipments(contact_id, direction, expected_at, status) VALUES (?, 'inbound', ?, 'delivered')",
               (vendor, d))
print("\n1. HiWR notices:", ag.suggest_missing(db))

aid = ag.propose(db, "vendor_supply", {
    "parties": {"business": "Sample Restaurant", "vendor": "Coastal Produce"},
    "items": [{"name": "Roma tomatoes", "unit": "25 lb case", "unit_cents": 3400},
              {"name": "Romaine", "unit": "24 ct case", "unit_cents": 4150}],
    "delivery_days": "Mon, Thu before 10 AM", "payment_terms_days": 15, "food": True,
    "shortage_remedy": "credit", "report_within_hours": 24, "starts_on": "2026-12-01", "ends_on": "2027-05-31",
}, NOW, reason="4 deliveries in November with no supply agreement on file")
card = ag.confirm_card(db, aid, "chef_marco")
print("2. Card for chef_marco:", card["status"], "| must fix:", card["must_fix"])
print("   Money:", card["money"], "|", card["money_rule"])

ag.revise(db, aid, {"quality_spec": "Produce at or below 41F on arrival, no soft or split fruit"}, "chef_marco", NOW,
          "added the receiving check")
try:
    ag.confirm(db, aid, "hiwr", NOW)
except PermissionError as e:
    print("3. HiWR tries to confirm:", e)
print("   chef_marco confirms:", ag.confirm(db, aid, "chef_marco", NOW, seen_hash=ag.latest(db, aid)["hash"]))
try:
    ag.money_decision(db, aid, "chef_marco", NOW)
except PermissionError as e:
    print("4. chef_marco approves money:", e)
print("   manager_jo approves:", ag.money_decision(db, aid, "manager_jo", NOW))
print("   accounting_sue approves:", ag.money_decision(db, aid, "accounting_sue", NOW))
h = ag.latest(db, aid)["hash"]
print("5. Vendor accepts by email:", ag.accept(db, aid, "vendor", "chef_marco", "email_reply", NOW, h))
print("   Owner signs:", ag.accept(db, aid, "business", "owner_alex", "e_signature", NOW, h))

print("\n6. Thursday delivery, invoice INV-2207: 8 of 10 cases of tomatoes, billed $36.00 instead of $34.00")
tid = ag.check_delivery(db, aid, [
    {"name": "Roma tomatoes", "qty_expected": 10, "qty": 8, "unit_cents": 3600},
    {"name": "Romaine", "qty_expected": 6, "qty": 6, "unit_cents": 4150}],
    "2026-12-03T09:40", "receiver_luis", invoice_ref="INV-2207", perishable=True)
t = db.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
fin = json.loads(t["financial"])
print(f"   {tid} priority {t['priority']}: {t['subject']}")
print(f"   contract {fin['contract_ref']} | variance {ag.usd(fin['variance_cents'])} | {fin['proposed_resolution']}")
print(f"   lands in {fin['lands_in']}; {fin['money']}")
print("   Links on the agreement:", ag.links(db, aid))

print("\n7. What cook_luis sees (money hidden):")
text = ag.render(db, aid, "cook_luis")
print("\n".join("   " + l for l in text.split("## Terms")[1].split("## HiWR notes")[0].strip().splitlines()))

print("\n8. Vendor raises romaine to $43.50 from January: a new version")
ag.revise(db, aid, {"items": [{"name": "Roma tomatoes", "unit": "25 lb case", "unit_cents": 3400},
                              {"name": "Romaine", "unit": "24 ct case", "unit_cents": 4350}]}, "chef_marco",
          "2026-12-20T10:00", "vendor price change")
print("   Status now:", ag.status(db, aid), "(old approvals and signatures don't carry over)")

print("\n9. Forms HiWR won't let through")
offer = ag.draft(db, "offer_letter", {
    "parties": {"business": "Sample Restaurant", "worker": "Dana"}, "role": "Prep cook", "pay_cents": 700,
    "pay_basis": "hourly", "tipped": False, "pay_day": "Every other Friday", "pay_place": "Direct deposit",
    "start_on": "2026-12-08", "schedule": "Tue-Sat, 7 AM to 3 PM", "at_will": True}, "chef_marco", NOW)
print("   Offer letter:", ag.confirm_card(db, offer, "owner_alex")["must_fix"])
build = ag.draft(db, "construction_client", {
    "parties": {"business": "Sample Builders", "owner": "Pat"}, "job_ref": "LAB-0003", "site": "Lot 12, sample address",
    "contract_sum_cents": 18_500_000, "draws": [{"phase": "Foundation", "pct": 20}, {"phase": "Framing", "pct": 30},
                                                {"phase": "Dry-in", "pct": 30}, {"phase": "Final", "pct": 20}],
    "change_orders_in_writing": True, "owner_occupied_existing_home": False, "warranty_months": 12,
    "starts_on": "2027-01-04", "ends_on": "2027-08-30"}, "owner_alex", NOW)
for m in ag.confirm_card(db, build, "owner_alex")["must_fix"]:
    print("   House build:", m)
nc = ag.draft(db, "non_compete", {
    "parties": {"business": "Sample Salon", "worker": "Sam"}, "role": "Stylist", "months": 36,
    "territory": "New Hanover County", "restricted_work": "Hair services for salon clients",
    "consideration": "none", "starts_on": "2026-12-01"}, "owner_alex", NOW)
c = ag.confirm_card(db, nc, "owner_alex")
print("   Non-compete:", c["must_fix"], "| note:", c["notes"])

print("\n" + ag.DISCLAIMER)
