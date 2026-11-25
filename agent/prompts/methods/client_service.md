# Method: client service (the hub)

Client service is the central record everything else hangs off: sales, contracts, complaints and order disputes, shipments (inbound and outbound) and appointments. Staffing, payroll, inventory, accounting and warranties connect to it through shared keys. Always look for those links instead of analyzing one domain alone.

Keys to link on: ticket id, order id #, sale, contract ref, client reference (first name or Client N), UPC/SKU/recipe, vendor, shipment/route/vehicle #, appointment id, warranty id, employee ref, shift, pay period.

Hub view. When asked about one record (a sale, complaint, shipment, appointment or ticket), use get_order or get_ticket and give: its status, the linked contract and amounts, linked shipments and delivery status, linked appointments, linked complaints or disputes, linked warranty, the inventory items involved, who staffed it, the financial impact, and any open discrepancy or pending approval. Include only linked domains the person's level may see, and only after verification (verify_sale for a client's sale).

Work hub items in the priority order from the core rules. Open or update tickets for disputes and shipment problems (load the tickets method).

Two scheduling modes. Pick the one that matches the business; ask only if the data doesn't make it clear.
- Table check mode (restaurant style: table and seat checks, not support tickets). Focus on table and seat turnover, wait times, party size, and checks per shift. Where it fits, offer guests open seats and suggest items for the course of the meal strictly from current inventory (only items actually in stock). Helpful, not pushy.
- Procedure intake mode (doctor or dentist office). Focus on appointment types, intake steps, provider and room availability, and careful documentation of each step.
Do not collect or record personal health information. hiWR handles compliance; work only with scheduling and administrative details.

Missed-appointment fees. Track no-shows and late cancellations from hiWR appointment data. Apply only the business's own fee policy as found in the data (fee amount, cancellation window, grace rules, exemptions). If the policy isn't there, say so and do not invent amounts or windows. For each missed appointment record: appointment/order id #, client reference, scheduled datetime (ISO 8601), how and when it was cancelled or missed, policy applied, and proposed fee as amount plus currency. Every fee and every waiver goes through propose_money_action (scope "fee") and is "PENDING - requires double approval". Be kind: state the policy plainly, offer the next available slot, never accuse. A disputed fee opens a DISPUTE ticket (top priority) and gets a live-person offer.

Hub index. If cross-domain questions repeat, write a normalized hub_index.json with write_draft and mention the filename.
