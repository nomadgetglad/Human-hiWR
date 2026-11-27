# Business agreements in HiWR

_Research notes and the form catalog behind `hiwr_agreements.py`. Templates, not legal advice: every form should
be reviewed by a North Carolina lawyer before a business relies on it._

## Why HiWR keeps agreements

Most of what goes wrong in a small business is a gap between what was agreed and what happened: a short
delivery, a price that crept up on the invoice, a client who says the job wasn't finished, a no-show who
disputes the fee. HiWR already measures what happened. An agreement on file is the other half: what should
have happened. With both, HiWR can spot the mismatch and open the ticket, and the ticket's financial block can
point at the clause it is about.

## The 14 forms

| Form | Who needs it | What HiWR checks | Tickets it feeds |
| --- | --- | --- | --- |
| Vendor supply agreement | Restaurant, retail, construction | Items and agreed prices, delivery days, shortage remedy; food needs a receiving quality spec (temps, NSF, food code) | SHIPMENT on short, wrong or overpriced deliveries |
| Purchase order | Everyone who buys | PO number, quantities, prices, deliver-by date | SHIPMENT |
| Client service agreement | Services, trades | Scope, price, deposit, schedule, changes only in writing, cancel notice and fee, warranty | DISPUTE |
| Catering and event agreement | Restaurant | Guest guarantee due before the event, menu, allergens named, deposit, cancel fee | DISPUTE |
| Appointment and late fee terms | Salons, clinics, services, reservations | Client agreed before booking, reminder at least 1 business day ahead, late and no-show fees | DISPUTE; fees are `fee` money actions |
| Job offer and wage notice | Every employer | Pay at or above $7.25/hour ($2.13 cash wage if tipped), pay day and how paid, start date, schedule | Payroll |
| Independent contractor agreement | Anyone using 1099 help | Deliverables, rate, insurance; flags when the business sets hours or supplies tools | DISPUTE |
| Subcontractor agreement | Construction (Lab) | Contract sum, retainage, draws add to 100%, lien waiver each draw, insurance, license at $40,000+ | DISPUTE, INSPECTION via Lab |
| Construction agreement with the owner | Construction (Lab) | Contract sum, draws, signed change orders, license and lien agent at $40,000+, warranty | DISPUTE |
| Confidentiality agreement (NDA) | Recipes, prices, client lists, plans | What is confidential, why it's shared, how long | DISPUTE |
| Non-compete / non-solicit | Salons, services, sales, trades | Something given in return, length (notes over 2 years, blocks 5+), territory, restricted work | none |
| Commercial lease | Any storefront | Rent, shared costs, deposit, term, repairs, renewal; flags a personal guarantee | DISPUTE |
| Equipment lease or rental | Restaurant, construction, retail | Rate, term, service, damage terms | DISPUTE |
| Warranty terms | Retail, trades, services | What's covered, how long, exclusions, how to claim, remedy | DISPUTE on each claim |

The temporary work agreement from `hiwr_staff.py` (`contract_dishwasher_temp.md`) stays where it is; it is a
shift-by-shift agreement tied to roll call and reliability scores.

## North Carolina points the checks are built on

Checked against the statutes on 2026-11-27. Laws change; confirm with a lawyer before relying on any of this.

- **Wage notice.** Employers must "notify its employees, in writing at the time of hiring, of the promised wages
  and the day and place for payment", and give written notice at least one pay period before a change in
  promised wages. HiWR makes pay, pay day and pay place required on the offer form.
  [G.S. 95-25.13](https://ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_95/GS_95-25.13.html)
- **General contractor license at $40,000.** Anyone who undertakes construction costing $40,000 or more is a
  general contractor and needs the license (owner-builders building for their own use are exempt). The
  construction forms require a license number at that amount.
  [G.S. 87-1](https://www.ncleg.gov/enactedlegislation/statutes/html/bysection/chapter_87/gs_87-1.html)
- **Mechanics lien agent at $40,000.** An owner improving property for $40,000 or more must name a lien agent no
  later than the first contract, except for an existing single-family home the owner lives in.
  [G.S. 44A-11.1](https://www.ncleg.gov/enactedlegislation/statutes/html/bysection/chapter_44a/gs_44a-11.1.html)
- **Non-competes.** NC courts enforce an employee non-compete only if it is in writing, part of the employment
  contract, based on valuable consideration (a new job, or something new for a current employee), reasonable in
  time and territory, and protects a legitimate business interest. Courts have generally approved up to two
  years; five years is presumed unreasonable.
  [Ward and Smith](https://www.wardandsmith.com/articles/north-carolina-non-compete-agreement-restrictions)
  (law firm article, 2014)
- **Minimum wage.** NC follows the federal $7.25/hour, with a $2.13 cash wage for tipped workers when tips make
  up the difference.
- **Sales of goods.** Under the UCC (G.S. 25-2-201), a sale of goods for $500 or more generally needs a signed
  writing to be enforced, which is one more reason to keep vendor supply agreements and POs on file.
- **Electronic signatures.** NC adopted the Uniform Electronic Transactions Act, so an e-signature or an emailed
  acceptance can count. HiWR records the method of each acceptance and the exact version hash the party saw.

## How a form moves through HiWR

1. **Draft.** Staff fill it, or HiWR proposes one (for example, a vendor with three or more deliveries and no
   supply agreement on file). Each change is a new version with its own hash.
2. **Confirm.** A person at the form's level (Manager for most, Account holder for leases, non-competes and
   construction) reviews the card: Confirm, Fix or Cancel. The checks run again on Confirm.
3. **Money terms.** Any dollar term means `PENDING - requires double approval`: two different certified
   Finance-level approvers, never the person who drafted, changed or confirmed it.
4. **Acceptance.** Each party's acceptance is recorded against the version they saw. Change a term later and
   everything above has to happen again.
5. **Active.** Deliveries and invoices are checked against it. A mismatch opens or updates a SHIPMENT ticket;
   a client complaint opens a DISPUTE. The ticket's financial block carries the contract ref, expected vs
   received, the variance and a proposed resolution, which stays in anticipated costs until a double-approved
   money action moves it. Renewals due in the next 30 days are listed for a person to decide.

Staff below Manager see the form without prices; pay terms on offers, contractor and non-compete forms are
Finance level.

## Not covered yet

Partnership or operating agreements, loans and equipment financing, franchise agreements, IP assignment,
online terms of sale and privacy policies, and insurance. These are less day-to-day and carry more legal risk
as templates. The next step for the forms themselves is a screen in the Human app (fill, confirm card,
approvals, signature capture).
