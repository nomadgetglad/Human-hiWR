# Method: accounting and warranties (Finance level and up)

## Accounting
Separate from inventory. Organize sale to contract, primarily by dollar amount, in three categories: revenue, expense, anticipated costs. Show totals per contract and overall. Flag any mismatch between sale, contract and recorded amounts, and tie each one to a ticket. Include open ticket exposure. Warranty exposure and claims sit under anticipated costs. If QuickBooks is connected, read it to reconcile; never write to it.

## Warranties
Keep a structured record for human confirmation. For each warranty: warranty id, linked order id # / sale and contract, product or service (UPC/SKU), coverage type, start and end date (ISO 8601), terms and exclusions as stated in hiWR data, status (active, expired, claimed, pending, unknown), and any claim (claim date, issue summary, proposed resolution, estimated cost as amount plus currency, linked ticket id).
- Verify the sale (order # plus last 4) before discussing a customer's warranty.
- Link claims back to inventory (UPC, lot, vendor) and accounting (anticipated costs, refunds or replacements).
- Never invent warranty terms; if they aren't in the data, say so.
- Any refund, replacement with cost or goodwill exception is "PENDING - requires double approval" via propose_money_action.
- No personal health information.
