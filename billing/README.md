# Human Software billing (Stripe)

How Human Software charges its own customers for Human hiWR: a one-time **setup fee**, a **monthly plan**, and
monthly add-ons per **extra module** and **extra location**, with **Stripe Tax** on all of it. This is separate
from the Human app a customer runs at their business; it runs where Human Software runs it (a small cloud server
or your desktop with a tunnel), because Stripe has to reach its webhook.

**Test mode only.** A live key (`sk_live_`/`rk_live_`) is refused until `BILLING_ALLOW_LIVE=yes` is set.

## What it does

| You want to | Call | What the customer sees |
| --- | --- | --- |
| Add a business | `POST /billing/customers` | nothing yet |
| Start a plan, pay by card or bank now | `POST /billing/checkout` | Stripe-hosted Checkout page: plan + add-ons, setup fee on the first payment, tax, their tax ID |
| Start a plan, pay by emailed invoice | `POST /billing/invoiced-subscription` | an emailed invoice each month with a pay link (card or ACH) |
| Bill a quoted setup fee on its own | `POST /billing/setup-invoice` | an emailed invoice with a pay link |
| Let them update a card, see invoices, cancel | `POST /billing/portal/{customer}` | Stripe customer portal (cancel at period end) |
| Refund | `POST /billing/refunds`, then two `POST /billing/refunds/{id}/decide` | nothing until two people approve |
| See the numbers | `GET /billing/summary` | subscriptions by status, open invoices, open DISPUTE tickets, refunds waiting |

Every route except the webhook needs `Authorization: Bearer <token>`. Tokens are in `BILLING_ADMINS`
(`name:token`), and the token decides who you are, so a refund's two approvers are always two other people.

**Refunds:** `PENDING - requires double approval`. The person who asks can't approve, each person decides once, one
rejection stops it, decisions are never changed, and the refund runs in Stripe on the second approval with an
idempotency key tied to the request, so it can never run twice. A refund made anywhere else (for example the
Stripe Dashboard) arrives by webhook and opens a DISPUTE ticket, "Refund made outside Human approvals".

**Disputes and fraud warnings** open a DISPUTE ticket held in anticipated costs, awaiting manager. When Stripe
closes the dispute the ticket moves to "resolved pending financial sync"; a person reconciles it. Ticket history
is append-only.

## Run it

```
pip install -r billing/requirements.txt
copy billing\.env.example billing\.env        (cp on Mac/Linux) and fill it in
python -m billing.config                      makes an admin token; run once per person
python -m billing.sandbox_check               end-to-end check against your test sandbox
python -m uvicorn billing.app:app --port 8100
stripe listen --forward-to localhost:8100/billing/webhook    prints the whsec_ for STRIPE_WEBHOOK_SECRET
```

`billing/.env` and `billing/billing.db` are never committed (the repo is public).

## Stripe setup, step by step

1. **Claim the sandbox.** A test sandbox was made on 2026-11-26 (its claim link is in the project thread, not
   here, because the repo is public). It expires **2026-12-03** unless claimed. Claiming makes it part of your Stripe account so you can open its
   Dashboard. (Or make a new sandbox in your own account: Dashboard > sandboxes.)
2. **Make a restricted key** (Dashboard > Developers > API keys > Create restricted key), not the full secret key.
   Write access to: Customers, Products, Prices, Checkout Sessions, Subscriptions, Invoices, Invoice items,
   Customer portal, Refunds, Payment methods. Read: Invoice payments, Tax settings and registrations, Events.
   Put it in `billing/.env` as `STRIPE_SECRET_KEY`.
3. **Payment methods** (Settings > Payment methods): cards are on; switch on **ACH Direct Debit** for bank
   payments. The code never lists payment methods, so what you switch on here is what customers see.
4. **Stripe Tax** (Settings > Tax), before the first real charge:
   - Set the **head office address** (Human Software in Wilmington, NC). Without it nothing is taxed and Stripe
     refuses `automatic_tax`.
   - Add a **registration** for each state where you're registered to collect sales tax. Adding it in Stripe
     only records it; registering with the state is separate. Without a registration Stripe collects **$0 with
     no error**. Which states apply is a question for your tax advisor; Stripe's threshold monitoring
     (Tax > Locations > Needs attention) flags states you may need as sales grow.
   - **Product tax codes** go in `.env`. Candidates from Stripe's list ([tax code guide](https://docs.stripe.com/tax/tax-codes)):
     - Monthly plan and add-ons: `txcd_10103001` Software as a service (SaaS), business use;
       `txcd_10105002` AI as a service, cloud based, business use; `txcd_10105004` AI as a service, cloud
       based and downloaded, business use (closest for on-site or hybrid installs).
     - Setup fee: `txcd_20120002` SaaS implementation and setup services, mandatory (setup required to buy);
       `txcd_20120001` the same, optional.
     Pick with your tax advisor; taxability differs by state.
   - **Tax behavior** `BILLING_TAX_BEHAVIOR`: `exclusive` (default) adds tax on top of the quoted price;
     `inclusive` means the quoted price already includes tax. This changes what customers pay.
5. **Customer portal** (Settings > Billing > Customer portal): turn on invoice history, payment method update,
   customer info (address, tax ID) and cancel at end of period. Plan changes stay with you (sales-led).
6. **Invoice branding** (Settings > Branding): logo and colors for invoices, Checkout and the portal.
7. **Webhook endpoint** (Developers > Webhooks) at `https://<your server>/billing/webhook` with these events:
   `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`,
   `customer.subscription.created`, `customer.subscription.updated`, `customer.subscription.deleted`,
   `invoice.finalized`, `invoice.paid`, `invoice.payment_failed`, `invoice.voided`, `invoice.marked_uncollectible`,
   `charge.refunded`, `charge.dispute.created`, `charge.dispute.closed`, `radar.early_fraud_warning.created`.
   Its signing secret goes in `STRIPE_WEBHOOK_SECRET`.
8. **Smart Retries and emails** (Settings > Billing > Revenue recovery): keep Stripe's defaults for failed-payment
   retries and reminder emails.

Then run `python -m billing.sandbox_check` again with tax on: each invoice line's `taxability_reason` must not be
`not_collecting` for a customer in a state where you have a registration.

## Where this came from

Stripe's implementation planner (run 2026-11-26 through the Stripe CLI, accepted) recommended: Billing with
hosted Checkout for subscriptions; the hosted invoice page with reminders for setup fees and invoiced customers;
invoices created by the API when a deal closes; Stripe's customer portal; Stripe's automatic retries; Stripe Tax
with collection; and webhook-driven updates into Human's own records (ACH included). Two deliberate differences:
customers are classic Stripe Customers (`cus_`), not the newer Accounts v2 the planner's portal example uses, and
the setup fee rides on the first subscription invoice when the plan starts at the same time.

## Before going live

- Live keys: a new restricted key in live mode, `BILLING_ALLOW_LIVE=yes`, a live webhook endpoint and secret.
- Live Tax settings and registrations (sandbox ones don't carry over), and the real prices (the `.env.example`
  amounts are samples).
- Stripe's [go-live checklist](https://docs.stripe.com/get-started/checklist/go-live).
