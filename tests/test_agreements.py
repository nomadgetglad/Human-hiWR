"""Business agreements: HiWR proposes, a person confirms, money terms need two certified approvers,
signatures are tied to the exact version, and deliveries/disputes open tickets against the agreement."""
import json
import sqlite3

import pytest

import hiwr_agreements as ag
from hiwr_access import set_level, grant_task
from hiwr_contacts import certify, add_contact, request_money_action
from hiwr_processes import close_ticket
from conftest import OWNER, TODAY

NOW = "2026-12-01T09:00"


@pytest.fixture
def adb(db):
    for m in ("manager_jo", "accounting_sue", "finance_kim"):
        for scope in ("contract", "fee", "subcontract"):
            certify(db, m, scope, OWNER)
    return db


def supply_fields(**kw):
    f = {"parties": {"business": "Sample Restaurant", "vendor": "Coastal Produce"},
         "items": [{"name": "Roma tomatoes", "unit": "case", "unit_cents": 3400},
                   {"name": "Romaine", "unit": "case", "unit_cents": 4150}],
         "delivery_days": "Mon, Thu", "payment_terms_days": 15, "food": True, "quality_spec": "41F or below",
         "shortage_remedy": "credit", "report_within_hours": 24, "starts_on": "2026-12-01", "ends_on": "2027-05-31"}
    f.update(kw)
    return f


def make_active(db, fields=None, by="chef_marco"):
    aid = ag.draft(db, "vendor_supply", fields or supply_fields(), by, NOW)
    ag.confirm(db, aid, "chef_marco", NOW)
    ag.money_decision(db, aid, "manager_jo", NOW)
    ag.money_decision(db, aid, "accounting_sue", NOW)
    h = ag.latest(db, aid)["hash"]
    ag.accept(db, aid, "vendor", "chef_marco", "email_reply", NOW, h)
    ag.accept(db, aid, "business", OWNER, "e_signature", NOW, h)
    assert ag.status(db, aid) == "active"
    return aid


# ---------- catalog ----------

def test_every_type_is_well_formed():
    kinds = {"text", "cents", "int", "pct", "date", "bool", "lines"}
    for key, t in ag.TYPES.items():
        assert t["roles"][0] == "business", key
        assert t["sign_level"] in (2, 3, 4)
        assert t["money_scope"] in (None, "contract", "fee", "payroll", "subcontract")
        for f in t["fields"]:
            assert f["kind"] in kinds or f["kind"].startswith("choice:"), (key, f)
        if any(f["kind"] == "cents" for f in t["fields"]):
            assert t["money_scope"], f"{key} has dollar terms but no approval scope"


def test_every_rendered_form_carries_the_lawyer_disclaimer(adb):
    for key in ag.TYPES:
        aid = ag.draft(adb, key, {"parties": {"business": "Sample"}}, "chef_marco", NOW)
        assert ag.DISCLAIMER in ag.render(adb, aid, OWNER)
        assert ag.confirm_card(adb, aid, OWNER)["disclaimer"] == ag.DISCLAIMER


def test_unknown_type_and_unknown_field_refused(adb):
    with pytest.raises(ValueError):
        ag.draft(adb, "handshake", {}, "chef_marco", NOW)
    aid = ag.draft(adb, "vendor_supply", supply_fields(secret_rebate=500), "chef_marco", NOW)
    with pytest.raises(ValueError, match="not a field"):
        ag.confirm(adb, aid, "chef_marco", NOW)


# ---------- HiWR proposes, a person confirms ----------

def test_hiwr_can_only_propose(adb):
    aid = ag.propose(adb, "vendor_supply", supply_fields(), NOW, "4 deliveries, nothing on file")
    with pytest.raises(ValueError):
        ag.draft(adb, "vendor_supply", supply_fields(), "hiwr", NOW)
    for call in (lambda: ag.confirm(adb, aid, "hiwr", NOW),
                 lambda: ag.money_decision(adb, aid, "hiwr", NOW),
                 lambda: ag.accept(adb, aid, "vendor", "hiwr", "email_reply", NOW, ag.latest(adb, aid)["hash"]),
                 lambda: ag.cancel(adb, aid, "hiwr", NOW)):
        with pytest.raises(PermissionError, match="a person"):
            call()
    assert ag.confirm_card(adb, aid, "chef_marco")["proposed_by"] == "HiWR"


def test_blocking_problem_stops_confirm(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(quality_spec=None), "chef_marco", NOW)
    assert ag.status(adb, aid) == "draft - needs fixes"
    with pytest.raises(ValueError, match="quality spec"):
        ag.confirm(adb, aid, "chef_marco", NOW)
    ag.revise(adb, aid, {"quality_spec": "41F"}, "chef_marco", NOW)
    assert ag.confirm(adb, aid, "chef_marco", NOW).startswith(ag.MONEY_PENDING)


def test_confirm_on_a_stale_card_refused(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    old = ag.confirm_card(adb, aid, "chef_marco")["hash"]
    ag.revise(adb, aid, {"payment_terms_days": 7}, "chef_marco", NOW)
    with pytest.raises(PermissionError, match="changed"):
        ag.confirm(adb, aid, "chef_marco", NOW, seen_hash=old)


def test_sign_level(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    with pytest.raises(PermissionError, match="Manager"):
        ag.confirm(adb, aid, "cook_luis", NOW)
    lease = ag.draft(adb, "commercial_lease", {
        "parties": {"business": "Sample", "landlord": "Sample Properties"}, "premises": "Unit 4",
        "rent_cents": 450000, "deposit_cents": 900000, "term_months": 36, "repairs": "Tenant: interior",
        "personal_guarantee": True, "starts_on": "2027-01-01", "ends_on": "2029-12-31"}, "chef_marco", NOW)
    with pytest.raises(PermissionError, match="Account holder"):
        ag.confirm(adb, lease, "manager_jo", NOW)
    assert "Personal guarantee" in " ".join(ag.confirm_card(adb, lease, OWNER)["notes"])
    ag.confirm(adb, lease, OWNER, NOW)


# ---------- money terms: double approval ----------

def test_money_needs_two_different_certified_approvers(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    with pytest.raises(PermissionError, match="confirm the form"):
        ag.money_decision(adb, aid, "manager_jo", NOW)
    ag.confirm(adb, aid, "chef_marco", NOW)
    assert ag.money_decision(adb, aid, "manager_jo", NOW) == "PENDING - requires double approval (1/2)"
    with pytest.raises(PermissionError, match="someone else"):
        ag.money_decision(adb, aid, "manager_jo", NOW)
    with pytest.raises(PermissionError, match="not certified"):
        ag.money_decision(adb, aid, "lead_rosa", NOW)
    assert ag.money_decision(adb, aid, "accounting_sue", NOW) == "APPROVED"
    with pytest.raises(PermissionError, match="already APPROVED"):
        ag.money_decision(adb, aid, "finance_kim", NOW)


def test_db_refuses_a_second_money_row_from_the_same_person(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    ag.confirm(adb, aid, "chef_marco", NOW)
    ag.money_decision(adb, aid, "manager_jo", NOW)
    v = ag.latest(adb, aid)
    with pytest.raises(sqlite3.IntegrityError):
        adb.execute("INSERT INTO agreement_events(agreement_id, version, hash, event, actor, at) "
                    "VALUES (?,?,?,?,?,?)", (aid, v["version"], v["hash"], "money_approve", "manager_jo", NOW))


@pytest.mark.parametrize("who", ["drafter", "confirmer", "reviser"])
def test_people_involved_cannot_approve_its_money(adb, who):
    drafter = "manager_jo" if who == "drafter" else "chef_marco"
    aid = ag.draft(adb, "vendor_supply", supply_fields(), drafter, NOW)
    if who == "reviser":
        ag.revise(adb, aid, {"payment_terms_days": 10}, "manager_jo", NOW)
    ag.confirm(adb, aid, "manager_jo" if who == "confirmer" else "chef_marco", NOW)
    with pytest.raises(PermissionError, match="cannot approve"):
        ag.money_decision(adb, aid, "manager_jo", NOW)


def test_demoted_approver_switched_off(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    ag.confirm(adb, aid, "chef_marco", NOW)
    set_level(adb, "finance_kim", 2, OWNER)
    with pytest.raises(PermissionError, match="not certified"):
        ag.money_decision(adb, aid, "finance_kim", NOW)


def test_rejection_is_final_for_that_version(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    ag.confirm(adb, aid, "chef_marco", NOW)
    assert ag.money_decision(adb, aid, "manager_jo", NOW, "reject") == "REJECTED"
    assert ag.status(adb, aid) == "money terms REJECTED"
    with pytest.raises(PermissionError):
        ag.money_decision(adb, aid, "accounting_sue", NOW)
    with pytest.raises(PermissionError, match="REJECTED"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, ag.latest(adb, aid)["hash"])


def test_contract_approval_task_is_owner_only_and_finance_only(adb):
    with pytest.raises(PermissionError, match="Finance"):
        grant_task(adb, "chef_marco", "contract_approval", OWNER, TODAY)
    with pytest.raises(PermissionError, match="account holder"):
        grant_task(adb, "finance_kim", "contract_approval", "manager_jo", TODAY)


def test_agreement_without_money_skips_approval(adb):
    aid = ag.draft(adb, "nda", {"parties": {"business": "Sample", "other_party": "Sample Investor"}, "mutual": False,
                                "what": "Recipes and supplier prices", "purpose": "Investment talks", "years": 3,
                                "starts_on": "2026-12-01"}, "chef_marco", NOW)
    assert ag.confirm(adb, aid, "chef_marco", NOW) == "awaiting acceptance: business, other_party"
    with pytest.raises(ValueError, match="no money"):
        ag.money_decision(adb, aid, "manager_jo", NOW)


# ---------- acceptance and versions ----------

def test_acceptance_rules(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    h = ag.latest(adb, aid)["hash"]
    with pytest.raises(PermissionError, match="confirm"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, h)
    ag.confirm(adb, aid, "chef_marco", NOW)
    with pytest.raises(PermissionError, match="double approval"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, h)
    ag.money_decision(adb, aid, "manager_jo", NOW)
    ag.money_decision(adb, aid, "accounting_sue", NOW)
    with pytest.raises(PermissionError, match="different version"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, "0" * 64)
    with pytest.raises(ValueError, match="not a party"):
        ag.accept(adb, aid, "landlord", "chef_marco", "email_reply", NOW, h)
    with pytest.raises(ValueError, match="method"):
        ag.accept(adb, aid, "vendor", "chef_marco", "smoke_signal", NOW, h)
    with pytest.raises(PermissionError, match="business signs"):
        ag.accept(adb, aid, "business", "lead_rosa", "in_person_signature", NOW, h)
    assert ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, h) == "awaiting acceptance: business"
    with pytest.raises(ValueError, match="already"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, h)
    assert ag.accept(adb, aid, "business", OWNER, "e_signature", NOW, h) == "active"
    ck = adb.execute("SELECT * FROM result_checkins WHERE subject=?", (f"agreement:{aid}",)).fetchone()
    assert ck and ck["works"] == 1


def test_a_change_after_signing_needs_everything_again(adb):
    aid = make_active(adb)
    old = ag.latest(adb, aid)["hash"]
    ag.revise(adb, aid, {"payment_terms_days": 30}, "chef_marco", NOW, "vendor asked for net 30")
    assert ag.status(adb, aid) == "draft - awaiting confirmation"
    ag.confirm(adb, aid, "chef_marco", NOW)
    assert ag.money_status(adb, aid).endswith("(0/2)")
    ag.money_decision(adb, aid, "manager_jo", NOW)
    ag.money_decision(adb, aid, "accounting_sue", NOW)
    with pytest.raises(PermissionError, match="different version"):
        ag.accept(adb, aid, "vendor", "chef_marco", "email_reply", NOW, old)


def test_history_is_append_only(adb):
    aid = make_active(adb)
    ag.link(adb, aid, "invoice", "INV-1", "chef_marco", NOW)
    for sql in ("UPDATE agreements SET title='x'", "DELETE FROM agreements",
                "UPDATE agreement_versions SET fields='{}'", "DELETE FROM agreement_versions",
                "UPDATE agreement_events SET actor='x'", "DELETE FROM agreement_events",
                "UPDATE agreement_links SET ref='x'", "DELETE FROM agreement_links"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            adb.execute(sql)


def test_cancelled_and_ended(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    with pytest.raises(PermissionError):
        ag.cancel(adb, aid, "cook_luis", NOW)
    ag.cancel(adb, aid, "chef_marco", NOW, "went with another vendor")
    assert ag.status(adb, aid) == "cancelled"
    with pytest.raises(PermissionError):
        ag.revise(adb, aid, {"payment_terms_days": 1}, "chef_marco", NOW)
    with pytest.raises(PermissionError):
        ag.confirm(adb, aid, "chef_marco", NOW)
    b = make_active(adb)
    ag.end(adb, b, OWNER, NOW, "term over")
    assert ag.status(adb, b) == "ended"


# ---------- form values ----------

@pytest.mark.parametrize("bad", [-100, True, 12.5, "50"])
def test_money_fields_are_whole_cents(adb, bad):
    aid = ag.draft(adb, "client_service", {
        "parties": {"business": "Sample", "client": "Pat"}, "scope_of_work": "Deep clean", "price_cents": bad,
        "schedule": "Dec 5", "change_orders_in_writing": True, "cancel_notice_hours": 24,
        "starts_on": "2026-12-05"}, "chef_marco", NOW)
    with pytest.raises(ValueError, match="Price"):
        ag.confirm(adb, aid, "chef_marco", NOW)


def test_item_prices_checked(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(items=[{"name": "Eggs", "unit": "case", "unit_cents": -1}]),
                   "chef_marco", NOW)
    with pytest.raises(ValueError, match="whole cents"):
        ag.confirm(adb, aid, "chef_marco", NOW)


def test_ends_before_start(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(ends_on="2026-01-01"), "chef_marco", NOW)
    with pytest.raises(ValueError, match="Ends before"):
        ag.confirm(adb, aid, "chef_marco", NOW)


# ---------- NC rules in the checks ----------

def offer(**kw):
    f = {"parties": {"business": "Sample", "worker": "Dana"}, "role": "Prep cook", "pay_cents": 1500,
         "pay_basis": "hourly", "tipped": False, "pay_day": "Friday", "pay_place": "Direct deposit",
         "start_on": "2026-12-08", "schedule": "Tue-Sat", "at_will": True}
    f.update(kw)
    return f


@pytest.mark.parametrize("pay,tipped,blocked", [(724, False, True), (725, False, False), (212, True, True),
                                                (213, True, False)])
def test_wage_floor(pay, tipped, blocked):
    msgs = [m for b, m in ag.problems("offer_letter", offer(pay_cents=pay, tipped=tipped)) if b]
    assert bool(msgs) == blocked


def test_offer_letter_needs_pay_day_and_place_and_shows_wage_notice_rule():
    assert any("Pay day" in m for b, m in ag.problems("offer_letter", offer(pay_day=None)) if b)
    assert any("95-25.13" in m for b, m in ag.problems("offer_letter", offer()) if not b)


def build(**kw):
    f = {"parties": {"business": "Sample Builders", "owner": "Pat"}, "job_ref": "LAB-1", "site": "Lot 12",
         "contract_sum_cents": 4_000_000, "draws": [{"phase": "All", "pct": 100}], "change_orders_in_writing": True,
         "owner_occupied_existing_home": False, "warranty_months": 12, "starts_on": "2027-01-04",
         "ends_on": "2027-06-01", "license_number": "12345", "lien_agent": "Sample Title Co"}
    f.update(kw)
    return f


def test_forty_thousand_dollar_rules():
    blocking = lambda f: [m for b, m in ag.problems("construction_client", f) if b]
    assert blocking(build()) == []
    assert any("87-1" in m for m in blocking(build(license_number=None)))
    assert any("44A-11.1" in m for m in blocking(build(lien_agent=None)))
    assert blocking(build(lien_agent=None, owner_occupied_existing_home=True)) == []
    assert blocking(build(contract_sum_cents=3_999_999, license_number=None, lien_agent=None)) == []
    assert any("100%" in m for m in blocking(build(draws=[{"phase": "A", "pct": 60}])))


def test_subcontract_license_and_waiver_notes():
    f = {"parties": {"business": "Sample Builders", "subcontractor": "Sample Electric"}, "job_ref": "LAB-1",
         "scope_of_work": "Rough and final electric", "contract_sum_cents": 4_500_000, "retainage_pct": 10,
         "draws": [{"phase": "Rough", "pct": 60}, {"phase": "Final", "pct": 40}], "lien_waiver_each_draw": False,
         "insurance_on_file": False, "starts_on": "2027-01-04", "ends_on": "2027-04-01"}
    p = ag.problems("subcontract", f)
    assert any(b and "87-1" in m for b, m in p)
    assert any(not b and "lien waiver" in m for b, m in p)
    assert any(not b and "insurance" in m for b, m in p)


def nc(**kw):
    f = {"parties": {"business": "Sample Salon", "worker": "Sam"}, "role": "Stylist", "months": 12,
         "territory": "New Hanover County", "restricted_work": "Hair services", "consideration": "new_job",
         "starts_on": "2026-12-01"}
    f.update(kw)
    return f


def test_non_compete_rules():
    blocking = lambda f: [m for b, m in ag.problems("non_compete", f) if b]
    notes = lambda f: [m for b, m in ag.problems("non_compete", f) if not b]
    assert blocking(nc()) == [] and notes(nc()) == []
    assert blocking(nc(consideration="none"))
    assert blocking(nc(consideration="raise_or_bonus"))
    assert blocking(nc(consideration="raise_or_bonus", bonus_cents=50000)) == []
    assert notes(nc(months=36)) and not blocking(nc(months=36))
    assert blocking(nc(months=60))


def test_appointment_terms_need_consent_and_a_reminder():
    f = {"parties": {"business": "Sample Salon", "client": "Pat"}, "late_minutes": 15, "late_fee_cents": 2000,
         "no_show_fee_cents": 4000, "cancel_notice_hours": 24, "reminder_business_days": 1, "client_consented": True}
    assert not [m for b, m in ag.problems("appointment_terms", f) if b]
    assert [m for b, m in ag.problems("appointment_terms", {**f, "client_consented": False}) if b]
    assert [m for b, m in ag.problems("appointment_terms", {**f, "reminder_business_days": 0}) if b]


def test_catering_count_due_before_event():
    f = {"parties": {"business": "Sample", "client": "Pat"}, "event_date": "2026-12-20", "guest_count": 40,
         "guarantee_due": "2026-12-20", "menu": "Buffet", "allergens": "2 nut allergies",
         "price_per_guest_cents": 4500, "deposit_cents": 50000}
    assert any("before the event" in m for b, m in ag.problems("catering_event", f) if b)


def test_contractor_misclassification_note():
    f = {"parties": {"business": "Sample", "contractor": "Sample Design"}, "scope_of_work": "Menu design",
         "rate_cents": 5000, "rate_basis": "hourly", "deliverables": "Print-ready menu", "contractor_sets_hours": False,
         "contractor_own_tools": True, "insurance_on_file": True, "starts_on": "2026-12-01"}
    assert any("employee" in m for b, m in ag.problems("independent_contractor", f) if not b)


# ---------- who sees money ----------

def test_money_hidden_by_level(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    assert "$34.00" in ag.render(adb, aid, "chef_marco")
    staff = ag.render(adb, aid, "cook_luis")
    assert "$34.00" not in staff and "hidden" in staff
    assert ag.confirm_card(adb, aid, "cook_luis")["money"] == "hidden at your level"
    assert "money" not in ag.register(adb, "cook_luis", TODAY)[0]
    o = ag.draft(adb, "offer_letter", offer(), "chef_marco", NOW)
    assert "$15.00" not in ag.render(adb, o, "chef_marco")        # pay is Finance level
    assert "$15.00" in ag.render(adb, o, "accounting_sue")
    denied = adb.execute("SELECT COUNT(*) FROM access_log WHERE actor='cook_luis' AND action LIKE 'denied:%'").fetchone()[0]
    assert denied >= 1


# ---------- tickets, invoices, accounting ----------

def test_matching_delivery_opens_nothing(adb):
    aid = make_active(adb)
    assert ag.check_delivery(adb, aid, [{"name": "Romaine", "qty_expected": 6, "qty": 6, "unit_cents": 4150}],
                             NOW, "receiver_luis", invoice_ref="INV-1") is None


def test_delivery_against_a_draft_refused(adb):
    aid = ag.draft(adb, "vendor_supply", supply_fields(), "chef_marco", NOW)
    with pytest.raises(PermissionError, match="not active"):
        ag.check_delivery(adb, aid, [], NOW, "receiver_luis")


def test_short_and_overpriced_delivery_opens_shipment_ticket(adb):
    aid = make_active(adb)
    tid = ag.check_delivery(adb, aid, [
        {"name": "Roma tomatoes", "qty_expected": 10, "qty": 8, "unit_cents": 3600},
        {"name": "Truffles", "qty_expected": 0, "qty": 1, "unit_cents": 9000}],
        NOW, "receiver_luis", invoice_ref="INV-2207", perishable=True)
    t = adb.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    fin = json.loads(t["financial"])
    assert t["type"] == "SHIPMENT" and tid.startswith("SHP-")
    assert fin["contract_ref"].startswith(f"AGR-{aid:04d} v1")
    assert fin["variance_cents"] == 2 * 3400 + 8 * 200 + 9000
    assert fin["lands_in"] == "anticipated costs"
    assert ag.MONEY_PENDING in fin["money"]
    assert ag.find(adb, "invoice", "INV-2207") == aid and ag.find(adb, "ticket", tid) == aid
    # same invoice again: the open ticket is updated, not duplicated
    again = ag.check_delivery(adb, aid, [{"name": "Roma tomatoes", "qty_expected": 10, "qty": 7, "unit_cents": 3400}],
                              NOW, "receiver_luis", invoice_ref="INV-2207")
    assert again == tid
    assert adb.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1
    assert adb.execute("SELECT COUNT(*) FROM ticket_events WHERE ticket_id=? AND event='updated'", (tid,)).fetchone()[0] == 1


def test_dispute_ticket_stays_open_until_money_is_settled(adb):
    aid = ag.draft(adb, "client_service", {
        "parties": {"business": "Sample", "client": "Pat"}, "scope_of_work": "Deep clean", "price_cents": 30000,
        "schedule": "Dec 5", "change_orders_in_writing": True, "cancel_notice_hours": 24,
        "starts_on": "2026-12-05"}, "chef_marco", NOW)
    tid = ag.open_dispute(adb, aid, "Client says the patio was skipped", 8000, NOW, "chef_marco", invoice_ref="INV-9",
                          proposed_resolution="redo patio, or refund $80.00")
    fin = json.loads(adb.execute("SELECT financial FROM tickets WHERE id=?", (tid,)).fetchone()[0])
    assert tid.startswith("DSP-") and fin["agreed_terms"] == {"Price": 30000}
    request_money_action(adb, "refund", 8000, "Refund patio", "chef_marco", ticket_id=tid)
    with pytest.raises(PermissionError, match="can't close"):
        close_ticket(adb, tid, OWNER, NOW)
    with pytest.raises(ValueError):
        ag.open_dispute(adb, aid, "x", -5, NOW, "chef_marco")


# ---------- HiWR suggestions and renewals ----------

def test_suggests_supply_agreement_for_regular_vendor(adb):
    vid = add_contact(adb, "Coastal Produce", OWNER, kind="vendor")["id"]
    for d in ("2026-11-03", "2026-11-10", "2026-11-17"):
        adb.execute("INSERT INTO shipments(contact_id, direction, expected_at) VALUES (?, 'inbound', ?)", (vid, d))
    assert ag.suggest_missing(adb) == [{"vendor": "Coastal Produce", "deliveries": 3, "suggest": "vendor_supply"}]
    make_active(adb)
    assert ag.suggest_missing(adb) == []


def test_renewals_due(adb):
    aid = make_active(adb)
    assert ag.renewals_due(adb, "2027-05-01") == [{"id": aid, "title": "Vendor supply agreement",
                                                   "ends_on": "2027-05-31", "days_left": 30}]
    assert ag.renewals_due(adb, "2027-04-01") == []
