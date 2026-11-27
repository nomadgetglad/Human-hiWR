# HiWR sample code

Working sample code for HiWR, the AI business-management system that grew out of the HiWR hiring app.
Everything here is plain Python 3 and SQLite from the standard library: no packages to install, no network,
no AI model calls. The maths and the rules live in code; a local model would only sit on top to explain
results and answer from these records.

All people, prices, fees and thresholds in the demos are **sample data**. Values marked "sample" are
placeholders for the business owner to set.

## Run it

```
cd hiwr
python run_all_demos.py      # runs every demo and checks it against its saved *_output.txt
python reschedule_demo.py    # or run any single demo
```

Tests (from the repo root): `pip install -r tests/requirements.txt` then `python -m pytest tests`.
They cover double approval, access levels, ticket and accounting sync, the UI top bar by role and the attacks
listed in `../APPROVALS_SECURITY.md`.

Each demo builds a fresh in-memory database from `schema.sql`. To keep data between runs, pass a file
path to `connect("hiwr.db")` (database files are ignored by git). `run_all_demos.py --update` rewrites
the saved outputs after you change a module on purpose.

## Rules that run through every module

- **History is never overwritten.** Every change is appended with who did it and when.
- **Money never moves on its own.** Refunds, fees, payroll and subcontract payments become money actions
  marked PENDING until two different certified approvers (not the requester) sign. Only plan owners certify
  approvers, and only people at Finance level (3) or higher. Decisions are final, and an action's amount can't
  change once requested. A ticket closes only when its money is rejected, or approved and posted once.
- **HiWR proposes, a person confirms.** Schedules, purchase orders, hires and sales are drafts.
- **Every result gets an authority check-in**: did it work, and did it have the intended consequence.
- **Access levels** (0 Staff to 4 Account holder) are set only by the account holder. Task grants never
  unlock sensitive data such as costs, payroll or client records.
- **Ticket types**: DISPUTE, SHIPMENT, STAFFING, INSPECTION.

## Modules

| File | What it does | Demo |
| --- | --- | --- |
| `schema.sql` | All tables and views: contacts, consent, orders and workflows, tickets, money actions, staff, inventory, recipes, jobs | used by every demo |
| `hiwr_contacts.py` | Core: client records (E.164 phones, language, per-purpose SMS consent with STOP), dedupe, orders as workflows of stages (restaurant, made-to-order boots, doctor's office check-ins), tickets, money actions and double approval | `demo.py` |
| `hiwr_scheduling.py` | Reminder calls, cancellation with late fee and sunk-cost estimate, rescheduling against staff availability with payroll, overtime and equipment costing | `reschedule_demo.py` |
| `hiwr_processes.py` | Clinic waitlists, prime-time table moves with upsell math, hot processes (food cooling, truck at the dock) and calculated ones (ship-by dates, production tests) that open tickets when they slip | `processes_demo.py` |
| `hiwr_hiring.py` | Staffing gap to STAFFING ticket, candidates ranked on job-related facts only (current certs, availability, experience, work style), swipe match; a person makes the hire | `hiring_demo.py` |
| `hiwr_roles.py` | Role vocabulary: 56 schedule roles across restaurant, retail, long jobs, factory, health and services, each with English and Spanish job titles (line cook, cook II, cocinera de línea all = `cook`), seniority words, duties in both languages, usual certs and how much related experience counts. House titles a manager adds are append-only. A title never sets a level or pay | `tests/test_roles.py` |
| `hiwr_human.py` | **Human**, the employee module: staff needed per day from appointments or production quota, draft schedule (cheapest first, overtime counted), materials vs stock with order-by dates, supply chain brief | `human_demo.py` (writes `supply_chain_brief.md`) |
| `hiwr_staff.py` | Human > **Staff**: HR requests, same-night fills by text, work agreements with terms for both sides (warnings and reliability scores, never docked pay), certified search (e.g. AWS welder + OSHA for third shift) | `staff_demo.py` (writes `contract_dishwasher_temp.md`) |
| `hiwr_access.py` | Access levels 0 to 4 and the task certification chain: grants one hop deep, capped to the certifier's expiry, revocation cascades | `access_demo.py` |
| `hiwr_warehouse.py` | Inventory Management > **Warehouse**: stocker's pull list (floor vs back stock, oldest dates first, aisle order); stockers see units, never costs | `inventory_demo.py` |
| `hiwr_retail.py` | Warehouse for managers: fast sellers, slow movers, days of supply, sale and markdown proposals | `retail_demo.py` |
| `hiwr_stew.py` | Inventory Management > **Stew**: recipe costing with sub-recipes, hidden ingredients and allergens ("no tomato" vs the salsa inside), sales deplete inventory, portions left | `inventory_demo.py` |
| `hiwr_ordering.py` | Manager ordering: reorder lists from predicted use through Stew recipes, whole cases by vendor UPC, order-by dates from lead times, one PO draft per vendor, back-stock suggestions | `ordering_demo.py` |
| `hiwr_accounting.py` | Accounting view (Finance level and up): each money movement posted once, HiWR proposes a class and a person confirms; food/beverage and labor %, prime cost, operating profit, contribution by department. QuickBooks stays the source of truth: HiWR compares and flags, never overwrites. Managers see percentages only, no payroll dollars | `accounting_demo.py` |
| `hiwr_prep.py` | Prep level: the station worker's screen (fryer, grill, welder): expected demand for the rest of the shift, what to make now and when | `prep_demo.py` |
| `hiwr_lab.py` | Inventory Management > **Lab**: long jobs like a house build: phases, inspection gates (a failed one opens an INSPECTION ticket), subcontracts with retainage, draws, lien waivers, crew payroll | `lab_demo.py` |
| `hiwr_cs.py` | Customer service tied to order lines (UPC, price, qty, specs). Conflicts are asked, not assumed; every change logs who asked and who confirmed | `cs_demo.py` |
| `hiwr_factory.py` | Factory line (pipe plant): owner-written SOPs as the AI's only source, per-unit check-throughs, quotas, rotation by station certs, foreman board and HR roll call | `factory_demo.py` |
| `hiwr_signing.py` | Prototype: signed money approvals (Ed25519 keys on the approver's device, registered by the account holder) in a hash chain, so a forged, replayed or edited approval fails `verified_status()`. Needs the `cryptography` package. See `../APPROVALS_SECURITY.md` | `../tests/test_signing.py` |
| `hiwr_pharmacy.py` | Prescription order status only (no drug names stored), pharmacist verify as a certified task, holds, return to stock after 10 days | `pharmacy_demo.py` |

Generated documents kept as examples: `supply_chain_brief.md`, `contract_dishwasher_temp.md`.

## Needs legal review before real use

These parts are working models and templates, **not legal advice**:

- **Work agreements** (`hiwr_staff.py`, `contract_dishwasher_temp.md`): template for an employment lawyer
  to review; wage, overtime, break and call-in pay rules vary by state.
- **HIPAA** (doctor's office check-ins in `hiwr_contacts.py`, `hiwr_processes.py`, `hiwr_pharmacy.py`):
  no clinical data is stored and access is logged, but real use needs a compliance review and a BAA with
  any SMS or cloud vendor.
- **Construction** (`hiwr_lab.py`): lien waivers, retainage limits and prompt-pay rules vary by state;
  have contract forms reviewed locally.
- **SMS consent** (`hiwr_contacts.py`): follows opt-in per purpose and STOP handling; check against
  TCPA and carrier rules before sending real messages.
