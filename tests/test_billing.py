"""Human Software billing (billing/): settings safety, the two-approver refund rule, webhooks, and the API.
Stripe is replaced by a fake here; billing/sandbox_check.py runs the same flows against a real test sandbox."""
import hashlib
import hmac
import json
import sqlite3
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from billing import app as billing_app
from billing import approvals, store, webhooks
from billing.config import settings

A_TOKEN, D_TOKEN, B_TOKEN = "a" * 30, "d" * 30, "b" * 30
WHSEC = "whsec_testsecret"
ENV = {"STRIPE_SECRET_KEY": "rk_test_fake", "STRIPE_WEBHOOK_SECRET": WHSEC,
       "BILLING_ADMINS": f"alexandre:{A_TOKEN},dad:{D_TOKEN},bookkeeper:{B_TOKEN}",
       "BILLING_PRICE_MONTHLY_CENTS": "19900", "BILLING_PRICE_MODULE_CENTS": "4900",
       "BILLING_PRICE_LOCATION_CENTS": "7900", "BILLING_PRICE_SETUP_CENTS": "150000", "BILLING_DB": ":memory:"}


# ---------- settings ----------

def test_live_key_refused_unless_allowed():
    with pytest.raises(RuntimeError, match="LIVE"):
        settings({**ENV, "STRIPE_SECRET_KEY": "sk_live_x"})
    assert settings({**ENV, "STRIPE_SECRET_KEY": "sk_live_x", "BILLING_ALLOW_LIVE": "yes"}).live


def test_short_admin_token_refused():
    with pytest.raises(RuntimeError, match="too short"):
        settings({**ENV, "BILLING_ADMINS": "alexandre:short"})


def test_tax_on_by_default():
    s = settings(ENV)
    assert s.automatic_tax and s.tax_behavior == "exclusive" and s.prices["setup"] == 150000


# ---------- refunds need two people ----------

@pytest.fixture
def db():
    return store.connect()


def test_refund_double_approval(db):
    aid = approvals.request_refund(db, "alexandre", "pi_1", 5000, "Wrong module billed")
    assert approvals.status(db, aid) == "PENDING - requires double approval (0/2)"
    with pytest.raises(PermissionError, match="asked for"):
        approvals.decide(db, aid, "alexandre", "approve")
    assert approvals.decide(db, aid, "dad", "approve").endswith("(1/2)")
    with pytest.raises(PermissionError, match="already decided"):
        approvals.decide(db, aid, "dad", "approve")
    with pytest.raises(PermissionError):
        approvals.mark_executed(db, aid, "re_early")
    assert approvals.decide(db, aid, "bookkeeper", "approve") == "APPROVED"
    approvals.mark_executed(db, aid, "re_1")
    assert approvals.status(db, aid) == "DONE"
    with pytest.raises(PermissionError, match="final"):
        approvals.decide(db, aid, "someone", "reject")


def test_one_rejection_stops_it(db):
    aid = approvals.request_refund(db, "alexandre", "pi_1", 5000, "Customer asked")
    approvals.decide(db, aid, "dad", "reject")
    assert approvals.status(db, aid) == "REJECTED"
    with pytest.raises(PermissionError):
        approvals.decide(db, aid, "bookkeeper", "approve")


def test_approvals_never_changed(db):
    aid = approvals.request_refund(db, "alexandre", "pi_1", 5000, "Customer asked")
    approvals.decide(db, aid, "dad", "approve")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE approvals SET decision='reject'")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM approvals")


@pytest.mark.parametrize("pi,amount,reason", [("ch_1", 100, "x y"), ("pi_1", 0, "ok"), ("pi_1", 100, " ")])
def test_bad_refund_requests(db, pi, amount, reason):
    with pytest.raises(ValueError):
        approvals.request_refund(db, "alexandre", pi, amount, reason)


# ---------- webhooks ----------

def ev(etype, obj, eid=None):
    return {"id": eid or f"evt_{etype}_{obj.get('id')}", "type": etype, "data": {"object": obj}}


def test_subscription_and_invoice_events(db):
    sub = {"id": "sub_1", "customer": "cus_1", "status": "active", "cancel_at_period_end": False,
           "items": {"data": [{"price": {"lookup_key": "hiwr_monthly_v1"}, "quantity": 1},
                              {"price": {"lookup_key": "hiwr_location_monthly_v1"}, "quantity": 2}]}}
    assert webhooks.handle(db, ev("customer.subscription.created", sub), notify=lambda m: None) == "subscription active"
    row = db.execute("SELECT * FROM subscriptions").fetchone()
    assert json.loads(row["items"])[1] == {"lookup_key": "hiwr_location_monthly_v1", "quantity": 2}
    inv = {"id": "in_1", "customer": "cus_1", "status": "paid", "total": 21390, "amount_paid": 21390,
           "total_taxes": [{"amount": 1490}], "parent": {"subscription_details": {"subscription": "sub_1"}}}
    webhooks.handle(db, ev("invoice.paid", inv), notify=lambda m: None)
    row = db.execute("SELECT * FROM invoices").fetchone()
    assert (row["subscription"], row["tax_cents"], row["status"]) == ("sub_1", 1490, "paid")
    notes = []
    webhooks.handle(db, ev("customer.subscription.deleted", {**sub, "status": "active"}), notify=notes.append)
    assert db.execute("SELECT status FROM subscriptions").fetchone()[0] == "canceled" and notes


def test_duplicate_event_handled_once(db):
    e = ev("charge.dispute.created", {"id": "dp_1", "charge": "ch_1", "amount": 21390, "reason": "fraudulent"})
    assert webhooks.handle(db, e, notify=lambda m: None) == "dispute ticket"
    assert webhooks.handle(db, e, notify=lambda m: None) == "duplicate"
    assert db.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_dispute_opens_ticket_with_pending_note(db):
    notes = []
    webhooks.handle(db, ev("charge.dispute.created", {"id": "dp_1", "charge": "ch_1", "amount": 21390,
                                                       "reason": "product_not_received"}), notify=notes.append)
    t = db.execute("SELECT * FROM tickets").fetchone()
    assert (t["type"], t["status"], t["lands_in"], t["amount_cents"]) == ("DISPUTE", "awaiting manager", "anticipated costs", 21390)
    assert "PENDING - requires double approval" in notes[0]
    webhooks.handle(db, ev("charge.dispute.closed", {"id": "dp_1", "status": "won"}), notify=lambda m: None)
    hist = [r["status"] for r in db.execute("SELECT status FROM ticket_events ORDER BY id")]
    assert hist == ["awaiting manager", "resolved pending financial sync"]      # appended, not overwritten


def test_refund_outside_approvals_opens_ticket(db):
    charge = {"id": "ch_9", "customer": "cus_1", "amount_refunded": 5000,
              "refunds": {"data": [{"id": "re_x", "metadata": {}}]}}
    assert "ticket opened" in webhooks.handle(db, ev("charge.refunded", charge), notify=lambda m: None)
    ours = {**charge, "id": "ch_10", "refunds": {"data": [{"id": "re_y", "metadata": {"hiwr_money_action": "1"}}]}}
    assert webhooks.handle(db, ev("charge.refunded", ours), notify=lambda m: None) == "refund recorded"


def test_unpaid_checkout_is_not_a_sale(db):
    s = {"id": "cs_1", "payment_status": "unpaid", "customer": "cus_1", "subscription": "sub_1"}
    assert webhooks.handle(db, ev("checkout.session.completed", s), notify=lambda m: None) == "waiting for bank payment"
    assert webhooks.handle(db, ev("checkout.session.async_payment_succeeded", {**s, "payment_status": "paid"}),
                           notify=lambda m: None) == "checkout paid"


# ---------- API with a fake Stripe ----------

class FakeStripe:
    def __init__(self):
        self.calls = []
        rec = self

        class Svc:
            def __init__(self, name, ret):
                self.name, self.ret = name, ret

            def __getattr__(self, method):
                def call(*args, params=None, options=None):
                    rec.calls.append((self.name, method, args, params, options))
                    return self.ret(method, args, params)
                return call

        self.v1 = NS(
            prices=Svc("prices", lambda m, a, p: NS(data=[]) if m == "list" else NS(id=f"price_{p['lookup_key']}" if p else "price", product="prod_setup")),
            products=Svc("products", lambda m, a, p: NS(id="prod_" + p["metadata"]["hiwr_catalog"])),
            customers=Svc("customers", lambda m, a, p: NS(id="cus_new")),
            checkout=NS(sessions=Svc("checkout", lambda m, a, p: NS(id="cs_1", url="https://checkout.stripe.com/x"))),
            subscriptions=Svc("subscriptions", lambda m, a, p: NS(id="sub_1", status="active", latest_invoice="in_1")),
            invoices=Svc("invoices", lambda m, a, p: NS(id="in_2", status="open", total=90000, hosted_invoice_url="https://invoice")),
            invoice_items=Svc("invoice_items", lambda m, a, p: NS(id="ii_1")),
            invoice_payments=Svc("invoice_payments", lambda m, a, p: NS(data=[NS(status="paid", payment=NS(type="payment_intent", payment_intent="pi_9"))])),
            refunds=Svc("refunds", lambda m, a, p: NS(id="re_1", status="succeeded")),
            billing_portal=NS(sessions=Svc("portal", lambda m, a, p: NS(url="https://billing.stripe.com/p"))),
        )

    def construct_event(self, payload, sig, secret):
        ts, _, v1 = sig.partition(",v1=")
        ts = ts.split("=", 1)[1]
        good = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(good, v1):
            import stripe
            raise stripe.SignatureVerificationError("bad", sig)

    def find(self, name, method):
        return [c for c in self.calls if c[0] == name and c[1] == method]


@pytest.fixture
def api():
    fake = FakeStripe()
    billing_app.CTX.clear()
    billing_app.CTX.update(s=settings(ENV), sc=fake, db=store.connect(), prices=None)
    yield TestClient(billing_app.app), fake
    billing_app.CTX.clear()


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_needs_admin_token(api):
    c, _ = api
    assert c.get("/billing/summary").status_code == 401
    assert c.get("/billing/summary", headers=H("x" * 30)).status_code == 401
    assert c.get("/billing/summary", headers=H(A_TOKEN)).json()["mode"] == "test"


def test_catalog_has_tax_behavior_and_lookup_keys(api):
    c, fake = api
    c.get("/billing/summary", headers=H(A_TOKEN))
    made = [p for (_, m, _, p, _) in fake.find("prices", "create")]
    assert {p["lookup_key"] for p in made} == {"hiwr_monthly_v1", "hiwr_module_monthly_v1", "hiwr_location_monthly_v1", "hiwr_setup_v1"}
    assert all(p["tax_behavior"] == "exclusive" and p["currency"] == "usd" for p in made)
    assert "recurring" not in next(p for p in made if p["lookup_key"] == "hiwr_setup_v1")


def test_checkout_params(api):
    c, fake = api
    r = c.post("/billing/checkout", headers=H(A_TOKEN), json={"customer": "cus_1", "modules": 2, "locations": 1, "setup_cents": 120000})
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com")
    p = fake.find("checkout", "create")[0][3]
    assert p["mode"] == "subscription" and p["automatic_tax"] == {"enabled": True}
    assert "payment_method_types" not in json.dumps(p)
    assert [l.get("quantity") for l in p["line_items"]] == [1, 2, 1, 1]
    assert p["line_items"][-1]["price_data"]["unit_amount"] == 120000
    assert p["integration_identifier"].startswith("human_hiwr_")


def test_invoiced_subscription_and_setup_invoice(api):
    c, fake = api
    r = c.post("/billing/invoiced-subscription", headers=H(A_TOKEN), json={"customer": "cus_1", "setup_cents": 0, "days_until_due": 10})
    p = fake.find("subscriptions", "create")[0][3]
    assert r.json()["first_invoice"] == "in_1" and p["collection_method"] == "send_invoice" and "add_invoice_items" not in p
    r = c.post("/billing/setup-invoice", headers=H(A_TOKEN), json={"customer": "cus_1", "setup_cents": 90000})
    assert r.json()["hosted_invoice_url"] == "https://invoice"
    assert [m for (n, m, *_) in fake.calls if n == "invoices"] == ["create", "finalize_invoice", "send_invoice"]
    assert c.post("/billing/setup-invoice", headers=H(A_TOKEN), json={"customer": "cus_1", "setup_cents": 0}).status_code == 422


def test_refund_runs_only_after_two_other_people(api):
    c, fake = api
    r = c.post("/billing/refunds", headers=H(A_TOKEN), json={"invoice": "in_5", "amount_cents": 5000, "reason": "Double billed"})
    aid = r.json()["id"]
    assert r.json()["status"].startswith("PENDING - requires double approval") and "alexandre" not in r.json()["approvers"]
    assert c.post(f"/billing/refunds/{aid}/decide", headers=H(A_TOKEN), json={"decision": "approve"}).status_code == 403
    assert c.post(f"/billing/refunds/{aid}/decide", headers=H(D_TOKEN), json={"decision": "approve"}).json()["status"].endswith("(1/2)")
    assert not fake.find("refunds", "create")
    r = c.post(f"/billing/refunds/{aid}/decide", headers=H(B_TOKEN), json={"decision": "approve"})
    assert r.json() == {"id": aid, "status": "DONE", "refund": "re_1"}
    (_, _, _, params, options), = fake.find("refunds", "create")
    assert params["payment_intent"] == "pi_9" and params["amount"] == 5000
    assert options["idempotency_key"] == f"hiwr-refund-{aid}"
    assert c.post(f"/billing/refunds/{aid}/run", headers=H(A_TOKEN)).status_code == 403


def _signed(payload: bytes):
    ts = str(int(time.time()))
    sig = hmac.new(WHSEC.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def test_webhook_signature(api):
    c, _ = api
    body = json.dumps(ev("charge.dispute.created", {"id": "dp_7", "charge": "ch_7", "amount": 100, "reason": "general"})).encode()
    assert c.post("/billing/webhook", content=body, headers={"Stripe-Signature": "t=1,v1=bad"}).status_code == 400
    r = c.post("/billing/webhook", content=body, headers=_signed(body))
    assert r.json() == {"received": True, "note": "dispute ticket"}
    s = c.get("/billing/summary", headers=H(A_TOKEN)).json()
    assert s["open_dispute_tickets"] == {"count": 1, "anticipated_cost_cents": 100}
