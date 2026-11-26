"""Calls to Stripe for Human Software's own billing: the catalog, customers, subscriptions (Checkout or
emailed invoices), setup-fee invoices, the customer portal and refunds.

Follows the accepted Stripe plan: Billing + hosted Checkout for subscriptions, the hosted invoice page for
setup fees and invoiced customers, the customer portal for self-service, Stripe Tax on every object, and
webhooks for everything that happens after (see webhooks.py). No payment_method_types anywhere: payment
methods (card, ACH bank transfer...) are switched on in the Dashboard.
"""
import stripe

from .config import API_VERSION, CHECKOUT_TAG

# One Product per thing a customer can buy; prices are found again by lookup_key, so setup is idempotent
CATALOG = {
    "monthly": {"product": "Human hiWR", "description": "AI business management: monthly plan, one location, pilot module",
                "lookup_key": "hiwr_monthly_v1", "recurring": True, "tax": "monthly"},
    "module": {"product": "Human hiWR: additional module", "description": "Each module added after the pilot",
               "lookup_key": "hiwr_module_monthly_v1", "recurring": True, "tax": "monthly"},
    "location": {"product": "Human hiWR: additional location", "description": "Each extra site on the same account",
                 "lookup_key": "hiwr_location_monthly_v1", "recurring": True, "tax": "monthly"},
    "setup": {"product": "Human hiWR setup and onboarding",
              "description": "Walkthrough, loading your records, access levels, training, on-site install if chosen",
              "lookup_key": "hiwr_setup_v1", "recurring": False, "tax": "setup"},
}


def client(s):
    return stripe.StripeClient(s.secret_key, stripe_version=API_VERSION)


def _tax_code(s, which):
    return s.tax_code_monthly if which == "monthly" else s.tax_code_setup


def ensure_catalog(sc, s):
    """Creates the products and prices that don't exist yet. Returns {name: price_id}. Amounts come from
    settings; an existing price is never changed (Stripe prices are immutable): bump the lookup_key's _vN."""
    found = {p.lookup_key: p for p in sc.v1.prices.list(params={"lookup_keys": [c["lookup_key"] for c in CATALOG.values()],
                                                                   "limit": 100}).data}
    out = {}
    for name, c in CATALOG.items():
        if c["lookup_key"] in found:
            out[name] = found[c["lookup_key"]].id
            continue
        amount = s.prices.get(name)
        if not amount or amount <= 0:
            raise ValueError(f"Set BILLING_PRICE_{name.upper()}_CENTS before creating the {c['product']} price")
        prod = {"name": c["product"], "description": c["description"], "metadata": {"hiwr_catalog": name}}
        code = _tax_code(s, c["tax"])
        if code:
            prod["tax_code"] = code
        product = sc.v1.products.create(params=prod, options={"idempotency_key": f"hiwr-product-{c['lookup_key']}"})
        price = {"product": product.id, "currency": "usd", "unit_amount": amount, "lookup_key": c["lookup_key"],
                 "tax_behavior": s.tax_behavior}
        if c["recurring"]:
            price["recurring"] = {"interval": "month"}
        out[name] = sc.v1.prices.create(params=price, options={"idempotency_key": f"hiwr-price-{c['lookup_key']}"}).id
    return out


def setup_product_id(sc, prices):
    return sc.v1.prices.retrieve(prices["setup"]).product


def create_customer(sc, business_name, email, address=None, hiwr_business_id=None):
    """address: {line1, city, state, postal_code, country}. A full US address lets Stripe Tax place them."""
    params = {"name": business_name, "email": email, "metadata": {}}
    if address:
        params["address"] = address
    if hiwr_business_id:
        params["metadata"]["hiwr_business_id"] = hiwr_business_id
    return sc.v1.customers.create(params=params)


def _subscription_lines(prices, modules, locations):
    if modules < 0 or locations < 0 or modules > 50 or locations > 50:
        raise ValueError("Modules and locations are 0 to 50")
    lines = [{"price": prices["monthly"], "quantity": 1}]
    if modules:
        lines.append({"price": prices["module"], "quantity": modules})
    if locations:
        lines.append({"price": prices["location"], "quantity": locations})
    return lines


def _setup_line(s, sc, prices, setup_cents):
    """The quoted setup fee as a one-time line. None = the catalog setup price; 0 = no setup fee."""
    if setup_cents is None:
        return {"price": prices["setup"], "quantity": 1}
    if setup_cents == 0:
        return None
    if setup_cents < 0:
        raise ValueError("Setup fee can't be negative")
    return {"price_data": {"currency": "usd", "product": setup_product_id(sc, prices), "unit_amount": setup_cents,
                           "tax_behavior": s.tax_behavior}, "quantity": 1}


def subscription_checkout(sc, s, prices, customer, modules=0, locations=0, setup_cents=None):
    """Hosted Checkout that starts the monthly plan and charges the setup fee on the first invoice.
    The customer pays by card or bank account, whatever is switched on in the Dashboard."""
    lines = _subscription_lines(prices, modules, locations)
    setup = _setup_line(s, sc, prices, setup_cents)
    if setup:
        lines.append(setup)
    return sc.v1.checkout.sessions.create(params={
        "mode": "subscription",
        "customer": customer,
        "line_items": lines,
        "automatic_tax": {"enabled": s.automatic_tax},
        "tax_id_collection": {"enabled": True},            # business customers can add their tax ID
        "customer_update": {"address": "auto", "name": "auto"},
        "billing_address_collection": "required",          # tax the address typed here, not an old one
        "success_url": f"{s.public_url}/billing/thanks?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{s.public_url}/billing/cancelled",
        "integration_identifier": CHECKOUT_TAG,
        "metadata": {"hiwr_modules": str(modules), "hiwr_locations": str(locations)},
    })


def invoiced_subscription(sc, s, prices, customer, modules=0, locations=0, setup_cents=None, days_until_due=15):
    """For customers who want an emailed invoice each month instead of a saved card. The first invoice also
    carries the setup fee. Stripe emails each invoice with a link to the hosted invoice page."""
    if not 1 <= days_until_due <= 90:
        raise ValueError("Days until due is 1 to 90")
    params = {
        "customer": customer,
        "items": _subscription_lines(prices, modules, locations),
        "collection_method": "send_invoice",
        "days_until_due": days_until_due,
        "automatic_tax": {"enabled": s.automatic_tax},
        "metadata": {"hiwr_modules": str(modules), "hiwr_locations": str(locations)},
    }
    setup = _setup_line(s, sc, prices, setup_cents)
    if setup:
        params["add_invoice_items"] = [setup]
    return sc.v1.subscriptions.create(params=params)


def setup_invoice(sc, s, prices, customer, setup_cents, memo=None, days_until_due=15, idempotency_key=None):
    """A one-off invoice for a quoted setup fee (when the subscription starts later or is billed apart).
    Finalized and emailed; the customer pays on the hosted invoice page."""
    if not setup_cents or setup_cents <= 0:
        raise ValueError("Setup fee must be more than $0")
    inv = sc.v1.invoices.create(params={
        "customer": customer, "collection_method": "send_invoice", "days_until_due": days_until_due,
        "automatic_tax": {"enabled": s.automatic_tax}, "pending_invoice_items_behavior": "exclude",
        **({"description": memo} if memo else {}),
        "metadata": {"hiwr_kind": "setup"},
    }, options={"idempotency_key": idempotency_key} if idempotency_key else None)
    sc.v1.invoice_items.create(params={
        "customer": customer, "invoice": inv.id,
        "price_data": {"currency": "usd", "product": setup_product_id(sc, prices), "unit_amount": setup_cents,
                       "tax_behavior": s.tax_behavior},
    }, options={"idempotency_key": f"{idempotency_key}-item"} if idempotency_key else None)
    inv = sc.v1.invoices.finalize_invoice(inv.id)
    return sc.v1.invoices.send_invoice(inv.id)


def portal_session(sc, s, customer):
    """Short-lived link where the customer updates their card or bank account, sees invoices, or cancels."""
    return sc.v1.billing_portal.sessions.create(params={"customer": customer, "return_url": f"{s.public_url}/billing"})


def ensure_portal_configuration(sc):
    """Invoice history, payment method update and cancel at period end. Plan changes stay with us (sales-led)."""
    return sc.v1.billing_portal.configurations.create(params={
        "features": {
            "invoice_history": {"enabled": True},
            "payment_method_update": {"enabled": True},
            "customer_update": {"enabled": True, "allowed_updates": ["address", "tax_id", "email", "name"]},
            "subscription_cancel": {"enabled": True, "mode": "at_period_end"},
        },
    })


def payment_intent_for_invoice(sc, invoice_id):
    """The PaymentIntent that paid an invoice, for a refund."""
    pays = sc.v1.invoice_payments.list(params={"invoice": invoice_id, "limit": 10}).data
    for p in pays:
        pay = p.payment
        if p.status == "paid" and getattr(pay, "type", None) == "payment_intent":
            return pay.payment_intent if isinstance(pay.payment_intent, str) else pay.payment_intent.id
    raise LookupError(f"Invoice {invoice_id} has no paid card or bank payment to refund")


def execute_refund(sc, action):
    """Runs an APPROVED refund. The idempotency key is tied to the money action, so it can never run twice.
    For a taxed invoice, Stripe Tax adjusts the tax it recorded for the refunded amount."""
    return sc.v1.refunds.create(params={
        "payment_intent": action["payment_intent"], "amount": action["amount_cents"],
        "metadata": {"hiwr_money_action": str(action["id"]), "reason": action["reason"][:450]},
    }, options={"idempotency_key": f"hiwr-refund-{action['id']}"})
