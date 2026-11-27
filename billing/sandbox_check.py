"""End-to-end check against a Stripe TEST sandbox (refuses live keys):
catalog, a customer, a Checkout link, an invoiced subscription, a setup-fee invoice paid with a test card,
and a refund that only runs after two approvals.

    python -m billing.sandbox_check
"""
from . import approvals, store, stripe_ops
from .config import settings


def main():
    s = settings()
    if s.live:
        raise SystemExit("sandbox_check only runs with a test key")
    sc = stripe_ops.client(s)
    db = store.connect()
    prices = stripe_ops.ensure_catalog(sc, s)
    print("catalog:", prices)

    cus = stripe_ops.create_customer(sc, "Sandbox Diner", "sandbox-diner@example.com",
                                     {"line1": "100 N Front St", "city": "Wilmington", "state": "NC",
                                      "postal_code": "28401", "country": "US"}, "sandbox-diner")
    print("customer:", cus.id)

    sess = stripe_ops.subscription_checkout(sc, s, prices, cus.id, modules=1, locations=1, setup_cents=120000)
    print("checkout:", sess.id, "amount_total", sess.amount_total, "tax", sess.total_details.amount_tax if sess.total_details else None)

    sub = stripe_ops.invoiced_subscription(sc, s, prices, cus.id, modules=2, locations=0, setup_cents=0)
    print("invoiced subscription:", sub.id, sub.status, "first invoice", sub.latest_invoice)

    inv = stripe_ops.setup_invoice(sc, s, prices, cus.id, 90000, memo="Setup for Sandbox Diner", days_until_due=10)
    print("setup invoice:", inv.id, inv.status, "total", inv.total, "taxes", inv.total_taxes, inv.hosted_invoice_url)
    for line in inv.lines.data:
        for t in (line.taxes or []):
            print("   line tax:", t.amount, t.taxability_reason)

    card = sc.v1.payment_methods.attach("pm_card_visa", params={"customer": cus.id})   # Stripe test card
    paid = sc.v1.invoices.pay(inv.id, params={"payment_method": card.id})
    print("paid:", paid.status)
    pi = stripe_ops.payment_intent_for_invoice(sc, inv.id)

    aid = approvals.request_refund(db, "alexandre", pi, 10000, "Sandbox check: partial refund", inv.id)
    print("refund request:", approvals.status(db, aid))
    try:
        approvals.decide(db, aid, "alexandre", "approve")
    except PermissionError as e:
        print("requester can't approve:", e)
    print("after 1st approval:", approvals.decide(db, aid, "dad", "approve"))
    print("after 2nd approval:", approvals.decide(db, aid, "bookkeeper", "approve"))
    ref = stripe_ops.execute_refund(sc, dict(db.execute("SELECT * FROM money_actions WHERE id=?", (aid,)).fetchone()))
    again = stripe_ops.execute_refund(sc, dict(db.execute("SELECT * FROM money_actions WHERE id=?", (aid,)).fetchone()))
    approvals.mark_executed(db, aid, ref.id)
    print("refund:", ref.id, ref.status, "| run twice gives the same refund:", again.id == ref.id, "|", approvals.status(db, aid))

    try:
        print("portal:", stripe_ops.portal_session(sc, s, cus.id).url[:40] + "...")
    except Exception as e:  # the portal needs a saved configuration in the Dashboard first
        print("portal: not configured yet:", str(e)[:160])


if __name__ == "__main__":
    main()
