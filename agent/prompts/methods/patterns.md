# Method: discrepancy pattern correlation

Don't just list discrepancies; correlate them. Use when several discrepancies are flagged or someone asks about trends, root causes or "what keeps going wrong". Only show patterns in domains the person's level may see.

a. Build one normalized discrepancy table (query_db plus tickets): discrepancy_id, ticket_id, type (payroll_hours, inventory_invoice, accounting_mismatch, client_service, shipment, appointment_fee, warranty), subtype (missed_clock_in, overtime_anomaly, qty_mismatch, upc_mismatch, sale_contract_amount_mismatch, order_dispute, complaint, shipment_delay, short_shipment, no_show, late_cancel, warranty_claim), occurred_at, employee_ref (first name or label only), role_or_station, shift (date, start/end, day of week), pay_period, manager_ref, item_ref, vendor_ref, contract_ref, order_id, expected_value, actual_value, difference, currency, source. Null for absent values.
b. Group and count along three axes, and cross them:
   - Person/shift: employee, role/station, manager, day of week, daypart (primetime vs not), pay period.
   - Item/vendor: UPC/SKU, recipe, vendor, contract, warranty product.
   - Time: day of week, time of day, pay-period boundaries (rotating cutoff, one-business-day rule), week of month, month and season, days around deliveries.
c. Look for recurring combinations (same employee + shift + subtype; same vendor + UPC + short shipments; same daypart + disputes + stock-outs), clustering around pay-period boundaries, and cross-type chains (short deliveries from one vendor, then 86'd items, then disputes and warranty claims on that item). Report each with count, share of all discrepancies, dollar impact where computable, and date range.
d. Be statistically honest: call it a pattern only at 3 or more occurrences or clearly above its share of volume; smaller clusters are "possible pattern, needs more data". State sample sizes. Correlation is not cause: give likely explanations for a human to confirm, each with a specific check or fix ("review the Thursday close schedule entries", "confirm vendor X's delivered quantity against the invoice"). Rank by dollar impact, then frequency.
e. Patterns involving employees are never disciplinary conclusions. Frame them as training gaps, schedule design or system classification problems. Any write-up is "PENDING - requires double approval".
f. Close the loop: record patterns a manager confirms or dismisses (add_ticket_note) and weight them later; note when a flagged pattern stopped after a fix.
