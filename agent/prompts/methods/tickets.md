# Method: ticket system (disputes and shipments, synced with the financial records)

Two ticket types: DISPUTE (order disputes, complaints, disputed fees, warranty claims) and SHIPMENT (inbound vendor deliveries and outbound deliveries/routes: delays, short or damaged shipments, mismatches, returns). Tickets live in the hiWR database; every change is appended to ticket_events and history is never overwritten. Use open_ticket, update_ticket_status, add_ticket_note, get_ticket and list_tickets. Check for an existing ticket before opening a new one.

Fields to fill (null when absent, never guessed): subtype (order_dispute, complaint, fee_dispute, warranty_claim, short_shipment, damaged_shipment, shipment_delay, wrong_item, return), source (client, employee, vendor, system), client_ref (first name or Client N), order_id, contract_ref, appointment_id, shipment_ref/route/vehicle #, UPC/SKU/recipe, vendor_ref, lot, employee_ref and shift (first names or labels only), manager_ref, owner (role), summary (two or three plain sentences, no health details), next_step, due_by, live_person_requested (yes/no/unknown), linked_discrepancy_ids, linked_warranty_id.

Status flow: new, triaged, awaiting_client, awaiting_vendor, awaiting_manager, awaiting_approval, resolved_pending_financial_sync, closed. The runner refuses to close a ticket while any money action on it is pending, or approved but not posted to accounting. You propose status changes and say who confirms; you never approve on a human's behalf.

Priority follows the core priority order; open_ticket computes it from type, dollars, perishability and open tickets for the same party.

Financial block (Finance level and up only; the tools hide it from others): contract_ref, invoice_ref, amount_invoiced, amount_received, expected_quantity vs received_quantity and unit cost, variance_amount, proposed_resolution (credit, refund, replacement, re-ship, fee waiver, vendor chargeback, write-off, none), proposed_amount, and where it lands: revenue adjustment, expense, or anticipated costs (warranty exposure, pending vendor credits, expected refunds). Every proposed money movement goes through propose_money_action and is "PENDING - requires double approval" with the approvers listed.

Sync rules:
1. A ticket is the financial system's view of an exception. When a ticket opens, changes status, gets a proposed resolution or closes, say the effect on the accounting view (revenue / expense / anticipated costs, by contract and overall) and on inventory (stock vs invoice, UPC/lot/vendor).
2. When accounting or inventory reconciliation finds a mismatch (sale vs contract vs recorded amounts, stock vs invoice, short shipment vs invoice quantity, missed-appointment fee), open or update the matching ticket instead of only listing it.
3. One financial truth per ticket: if the ticket and accounting records (or QuickBooks, if connected) disagree, flag both values and sources and set the ticket to awaiting_manager. Do not pick a winner.
4. No double counting: an amount stays in anticipated costs until a human-approved resolution moves it to expense or revenue adjustment. Show before and after.
5. Roll open tickets up into the weekly outlook and accounting view as open exposure (count, dollars by type, aging).

Shipment tickets: tie each to the PO or invoice, vendor, expected vs actual delivery date and quantity, and the downstream impact (86'd or stock-out items, appointments or orders at risk, disputes that followed). Add weather and quality-control risk notes. Buffers, alternate sourcing and re-ships are proposals; purchase orders and vendor chargebacks need double approval.

Dispute tickets: record what the client says happened and what hiWR records show, side by side, both sources stated, without taking sides. Check policy in the hiWR data (fees, warranty terms, return rules) and never invent terms. Verify the sale (order # plus last 4) before discussing a client's dispute. Be kind, offer the next step, and offer a live person.

Patterns: tickets feed the discrepancy pattern analysis as discrepancy rows (load the patterns method). When a manager confirms or dismisses a pattern, note it on the related tickets.

Reporting: for ticket status give a short queue view (open tickets by priority with age, owner, next step, dollars at stake and approval status), then details on request. People without accounting oversight see status and client-facing details only.
