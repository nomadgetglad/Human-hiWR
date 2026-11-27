"""Human Software billing service: charges Human hiWR customers (setup fee + monthly plan + add-ons) with Stripe.

Run from the repo root:   python -m uvicorn billing.app:app --port 8100
Needs billing/.env (see .env.example). Test mode only unless BILLING_ALLOW_LIVE=yes.

Every route except the webhook needs an admin token (Authorization: Bearer <token>) from BILLING_ADMINS.
The token decides who you are, so the two approvers on a refund are always two different people.
"""
import hmac
import threading

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import approvals, store, stripe_ops, webhooks
from .config import settings

import stripe

app = FastAPI(title="Human Software billing")
LOCK = threading.Lock()
CTX = {}


def ctx():
    """Settings, Stripe client, local db and catalog prices; loaded on first use (tests swap them in)."""
    if not CTX:
        s = settings()
        sc = stripe_ops.client(s)
        CTX.update(s=s, sc=sc, db=store.connect(s.db_path), prices=None)
    if CTX["prices"] is None:
        CTX["prices"] = stripe_ops.ensure_catalog(CTX["sc"], CTX["s"])
    return CTX


def admin(authorization):
    token = (authorization or "")[7:] if (authorization or "").lower().startswith("bearer ") else ""
    for t, name in CTX["s"].admins.items():
        if token and hmac.compare_digest(token, t):
            return name
    raise HTTPException(401, "Billing admin token needed")


def stripe_error(e):
    return HTTPException(402 if isinstance(e, stripe.CardError) else 400, getattr(e, "user_message", None) or str(e))


class Address(BaseModel):
    line1: str
    line2: str | None = None
    city: str
    state: str = Field(min_length=2, max_length=2)
    postal_code: str
    country: str = "US"


class NewCustomer(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=254)
    address: Address | None = None
    hiwr_business_id: str | None = None


class Plan(BaseModel):
    customer: str = Field(pattern=r"^cus_")
    modules: int = Field(0, ge=0, le=50)
    locations: int = Field(0, ge=0, le=50)
    setup_cents: int | None = Field(None, ge=0)   # None = catalog setup price, 0 = no setup fee
    days_until_due: int = Field(15, ge=1, le=90)


class SetupInvoice(BaseModel):
    customer: str = Field(pattern=r"^cus_")
    setup_cents: int = Field(gt=0)
    memo: str | None = Field(None, max_length=500)
    days_until_due: int = Field(15, ge=1, le=90)


class RefundRequest(BaseModel):
    invoice: str | None = Field(None, pattern=r"^in_")
    payment_intent: str | None = Field(None, pattern=r"^pi_")
    amount_cents: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=450)


class Decision(BaseModel):
    decision: str = Field(pattern=r"^(approve|reject)$")


@app.post("/billing/customers")
def new_customer(c: NewCustomer, authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        who = admin(authorization)
        try:
            cus = stripe_ops.create_customer(x["sc"], c.business_name, c.email,
                                             c.address.model_dump(exclude_none=True) if c.address else None,
                                             c.hiwr_business_id)
        except stripe.StripeError as e:
            raise stripe_error(e)
        x["db"].execute("INSERT INTO customers VALUES (?,?,?,?,?)", (cus.id, c.business_name, c.email, who, store.now()))
        x["db"].commit()
        return {"customer": cus.id}


@app.post("/billing/checkout")
def checkout(p: Plan, authorization: str = Header(None)):
    """A Checkout link for the customer: monthly plan + add-ons, setup fee on the first payment."""
    with LOCK:
        x = ctx()
        admin(authorization)
        try:
            sess = stripe_ops.subscription_checkout(x["sc"], x["s"], x["prices"], p.customer, p.modules, p.locations, p.setup_cents)
        except stripe.StripeError as e:
            raise stripe_error(e)
        return {"url": sess.url, "session": sess.id, "note": "Short-lived link: open it now or make a new one later"}


@app.post("/billing/invoiced-subscription")
def invoiced(p: Plan, authorization: str = Header(None)):
    """Monthly plan billed by emailed invoice (card or bank transfer on the hosted invoice page)."""
    with LOCK:
        x = ctx()
        admin(authorization)
        try:
            sub = stripe_ops.invoiced_subscription(x["sc"], x["s"], x["prices"], p.customer, p.modules, p.locations,
                                                   p.setup_cents, p.days_until_due)
        except stripe.StripeError as e:
            raise stripe_error(e)
        return {"subscription": sub.id, "status": sub.status, "first_invoice": webhooks._id(sub.latest_invoice)}


@app.post("/billing/setup-invoice")
def setup_invoice(i: SetupInvoice, authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        admin(authorization)
        try:
            inv = stripe_ops.setup_invoice(x["sc"], x["s"], x["prices"], i.customer, i.setup_cents, i.memo, i.days_until_due)
        except stripe.StripeError as e:
            raise stripe_error(e)
        return {"invoice": inv.id, "status": inv.status, "total_cents": inv.total, "hosted_invoice_url": inv.hosted_invoice_url}


@app.post("/billing/portal/{customer}")
def portal(customer: str, authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        admin(authorization)
        try:
            return {"url": stripe_ops.portal_session(x["sc"], x["s"], customer).url}
        except stripe.StripeError as e:
            raise stripe_error(e)


# ---------- refunds: PENDING - requires double approval ----------

@app.post("/billing/refunds")
def request_refund(r: RefundRequest, authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        who = admin(authorization)
        pi = r.payment_intent
        if r.invoice and not pi:
            try:
                pi = stripe_ops.payment_intent_for_invoice(x["sc"], r.invoice)
            except LookupError as e:
                raise HTTPException(400, str(e))
        if not pi:
            raise HTTPException(400, "Give the invoice (in_...) or the payment (pi_...)")
        try:
            aid = approvals.request_refund(x["db"], who, pi, r.amount_cents, r.reason, r.invoice)
        except ValueError as e:
            raise HTTPException(400, str(e))
        x["db"].commit()
        return {"id": aid, "status": approvals.status(x["db"], aid),
                "approvers": [n for n in x["s"].admins.values() if n != who]}


@app.get("/billing/refunds")
def refunds(authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        admin(authorization)
        return approvals.listing(x["db"])


@app.post("/billing/refunds/{aid}/decide")
def decide(aid: int, d: Decision, authorization: str = Header(None)):
    """The second approval runs the refund in Stripe, once (idempotency key per money action)."""
    with LOCK:
        x = ctx()
        who = admin(authorization)
        try:
            st = approvals.decide(x["db"], aid, who, d.decision)
        except LookupError as e:
            raise HTTPException(404, str(e))
        except (PermissionError, ValueError) as e:
            raise HTTPException(403, str(e))
        x["db"].commit()
        if st != "APPROVED":
            return {"id": aid, "status": st}
        action = dict(x["db"].execute("SELECT * FROM money_actions WHERE id=?", (aid,)).fetchone())
        try:
            ref = stripe_ops.execute_refund(x["sc"], action)
        except stripe.StripeError as e:
            # Approved but Stripe said no: stays APPROVED (not DONE); deciding again is refused, retry with /run
            raise stripe_error(e)
        approvals.mark_executed(x["db"], aid, ref.id)
        x["db"].commit()
        return {"id": aid, "status": approvals.status(x["db"], aid), "refund": ref.id}


@app.post("/billing/refunds/{aid}/run")
def run_approved(aid: int, authorization: str = Header(None)):
    """Retry an APPROVED refund that Stripe refused the first time (same idempotency key, so never twice)."""
    with LOCK:
        x = ctx()
        admin(authorization)
        if approvals.status(x["db"], aid) != "APPROVED":
            raise HTTPException(403, "Only an approved refund that hasn't run can be retried")
        action = dict(x["db"].execute("SELECT * FROM money_actions WHERE id=?", (aid,)).fetchone())
        try:
            ref = stripe_ops.execute_refund(x["sc"], action)
        except stripe.StripeError as e:
            raise stripe_error(e)
        approvals.mark_executed(x["db"], aid, ref.id)
        x["db"].commit()
        return {"id": aid, "status": "DONE", "refund": ref.id}


# ---------- what the accounting view and weekly outlook read ----------

@app.get("/billing/summary")
def summary(authorization: str = Header(None)):
    with LOCK:
        x = ctx()
        admin(authorization)
        d = x["db"]
        subs = d.execute("SELECT status, COUNT(*) n FROM subscriptions GROUP BY status").fetchall()
        open_inv = d.execute("SELECT COUNT(*) n, COALESCE(SUM(total_cents - amount_paid_cents),0) due FROM invoices "
                             "WHERE status='open'").fetchone()
        tickets = d.execute("SELECT COUNT(*) n, COALESCE(SUM(amount_cents),0) c FROM tickets WHERE status!='closed'").fetchone()
        pending = [a for a in approvals.listing(d) if a["status"].startswith("PENDING")]
        return {"mode": "LIVE" if x["s"].live else "test",
                "subscriptions": {r["status"]: r["n"] for r in subs},
                "open_invoices": {"count": open_inv["n"], "due_cents": open_inv["due"]},
                "open_dispute_tickets": {"count": tickets["n"], "anticipated_cost_cents": tickets["c"]},
                "refunds_pending_approval": len(pending)}


# ---------- Stripe -> us ----------

@app.post("/billing/webhook")
async def webhook(request: Request, stripe_signature: str = Header(None)):
    payload = await request.body()
    with LOCK:
        x = ctx()
        secret = x["s"].webhook_secret
        if not secret:
            raise HTTPException(503, "STRIPE_WEBHOOK_SECRET is not set")
        try:
            event = webhooks.verify(x["sc"], payload, stripe_signature, secret)
        except (ValueError, stripe.SignatureVerificationError):
            raise HTTPException(400, "Bad signature")
        note = webhooks.handle(x["db"], event)
        x["db"].commit()
        return {"received": True, "note": note}


@app.get("/billing/thanks", response_class=PlainTextResponse)
def thanks():
    return "Thank you. Your Human hiWR plan is being set up; you'll get a receipt by email."


@app.get("/billing/cancelled", response_class=PlainTextResponse)
def cancelled():
    return "No payment was taken. Your Human Software contact can send you a new link."
