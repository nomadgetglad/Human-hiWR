"""Stripe webhook events -> local billing records and DISPUTE tickets.

Subscriptions change after Checkout (renewals, failed payments, cancellations from the portal), so this is
where state is kept current, never from the success page. Every event is handled once (Stripe can retry).
"""
import json

from . import store

HANDLED = (
    "checkout.session.completed", "checkout.session.async_payment_succeeded", "checkout.session.async_payment_failed",
    "customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted",
    "invoice.finalized", "invoice.paid", "invoice.payment_failed", "invoice.voided", "invoice.marked_uncollectible",
    "charge.refunded", "charge.dispute.created", "charge.dispute.closed", "radar.early_fraud_warning.created",
)


def g(obj, *path):
    """Safe nested get on event JSON (fields moved between API versions)."""
    for p in path:
        if obj is None:
            return None
        obj = obj.get(p) if isinstance(obj, dict) else None
    return obj


def _id(v):
    return v.get("id") if isinstance(v, dict) else v


def _sub_items(sub):
    return [{"lookup_key": g(i, "price", "lookup_key") or g(i, "price", "id"), "quantity": i.get("quantity", 1)}
            for i in g(sub, "items", "data") or []]


def _invoice_subscription(inv):
    return (_id(g(inv, "parent", "subscription_details", "subscription")) or _id(inv.get("subscription")))


def _invoice_tax(inv):
    taxes = inv.get("total_taxes")
    if taxes is not None:
        return sum(t.get("amount", 0) for t in taxes)
    return inv.get("tax") or 0


def handle(db, event, notify=print):
    """event: the verified event as a dict. Returns a short note of what changed (for logs and tests)."""
    etype, obj = event["type"], g(event, "data", "object") or {}
    if etype not in HANDLED:
        return "ignored"
    if store.seen_event(db, event["id"], etype):
        return "duplicate"

    if etype.startswith("checkout.session."):
        if etype == "checkout.session.async_payment_failed":
            notify(f"Bank payment failed for checkout {obj.get('id')} (customer {_id(obj.get('customer'))})")
            return "async payment failed"
        # Paid now (card) or later (bank debit): only a paid session counts as a sale
        if obj.get("payment_status") not in ("paid", "no_payment_required"):
            return "waiting for bank payment"
        notify(f"New subscription {_id(obj.get('subscription'))} for customer {_id(obj.get('customer'))}")
        return "checkout paid"

    if etype.startswith("customer.subscription."):
        status = "canceled" if etype.endswith("deleted") else obj.get("status")
        store.upsert_subscription(db, obj["id"], _id(obj.get("customer")), status, _sub_items(obj),
                                  obj.get("cancel_at_period_end"))
        if status in ("past_due", "unpaid", "canceled"):
            notify(f"Subscription {obj['id']} is {status}")
        return f"subscription {status}"

    if etype.startswith("invoice."):
        store.upsert_invoice(db, obj["id"], _id(obj.get("customer")), _invoice_subscription(obj), obj.get("status"),
                             obj.get("total"), _invoice_tax(obj), obj.get("amount_paid"), obj.get("hosted_invoice_url"))
        if etype == "invoice.payment_failed":
            notify(f"Payment failed on invoice {obj['id']}; Stripe retries it and the customer can update their "
                   f"payment method in the portal")
        return f"invoice {obj.get('status')}"

    if etype == "charge.refunded":
        refunds = g(obj, "refunds", "data") or []
        ours = [r for r in refunds if g(r, "metadata", "hiwr_money_action")]
        if refunds and not ours:
            # Accounting -> ticket: money left outside the two-approver rule (for example from the Dashboard)
            tid, new = store.open_ticket(db, f"refund:{obj['id']}", _id(obj.get("customer")), None, obj["id"],
                                         obj.get("amount_refunded", 0), "Refund made outside Human approvals")
            notify(f"Refund on {obj['id']} was not approved in Human billing; ticket #{tid} awaiting manager")
            return "refund outside approvals: ticket opened"
        return "refund recorded"

    if etype in ("charge.dispute.created", "radar.early_fraud_warning.created"):
        charge = _id(obj.get("charge"))
        amount = obj.get("amount") or 0
        reason = obj.get("reason") or obj.get("fraud_type") or "dispute"
        tid, new = store.open_ticket(db, obj["id"], None, None, charge, amount, reason)
        if new:
            notify(f"DISPUTE ticket #{tid}: {reason} on {charge}, ${amount / 100:,.2f} held in anticipated costs. "
                   f"Accepting or refunding it is PENDING - requires double approval")
        return "dispute ticket"

    if etype == "charge.dispute.closed":
        st = obj.get("status")
        store.ticket_status(db, obj["id"], "resolved pending financial sync",
                            f"Stripe closed the dispute: {st}. A person reconciles it before the ticket closes")
        return f"dispute closed {st}"
    return "ignored"


def verify(sc, payload: bytes, sig_header, secret):
    """Checks the Stripe signature, then returns the event as plain JSON."""
    sc.construct_event(payload, sig_header, secret)
    return json.loads(payload)
