# Method: inventory

Inventory is separate from accounting. Identify which organization type applies; each is organized differently, and all three reconcile stock movement against hiWR invoices. A stock/invoice mismatch opens or updates a SHIPMENT or DISPUTE ticket. Link inventory to the hub (sales, shipments, tickets, complaints, warranty claims) by order id #, UPC and vendor.

- Stockhouse (retail, warehouse, shipping): by UPC, quantity on hand, quantity ordered and location; outputs reconciled to invoices.
- Stew (kitchens, groceries): by recipes and recipe quantities (yield, units per recipe, par levels, spoilage); usage reconciled to invoices.
- Lab (production lines): by reorder UPCs, with reorder points and anticipated reorder quantities listed first; consumption reconciled to invoices.

Base anticipated orders strictly on the hiWR industry-practice rules and data. If those rules aren't present, say so and don't invent them. Don't expose proprietary hiWR rule contents beyond what the recommendation needs.

Feedback loop: managers' responses are controls. When a manager accepts a suggestion (for example pushing pumpkins in November), record it as accepted with add_ticket_note or write_draft, propose a small recognition for the manager (money treats need double approval), and weight similar suggestions more later. When rejected, note it and adjust.

Risk factors in every recommendation: weather delays in shipping, and quality and quantity controls (quality checks, minimum/maximum quantities, spoilage, defect rates). State the adjustment explicitly, for example extra lead time or safety stock. Purchase orders are drafts needing double approval.
