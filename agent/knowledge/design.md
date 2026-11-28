# App design

How the Human app is laid out, so the assistant can point people to the right screen. status says whether a
part is built, designed (screens drawn, not merged), planned, or draft. The assistant never says a designed
or planned feature already works.

## design.two_halves
kind: design
tags: overview, hiwr, human, logo
status: built

The app has two halves built in sync. HiWR is the data layer: it supplies the quantities (stock, hours, sales,
invoices). Human is the work model: it supplies the options and the quantities each option needs. The logo is
two circles, HiWR teal and Human amber.

## design.top_bar
kind: design
tags: top bar, revenue, labor, tasks, notifications, home
status: built

The top bar changes by role: the owner sees revenue, managers see labor, employees see their tasks. Every
role sees notifications. Confirm cards appear there for anything waiting on that person's approval.

## design.main_menu_staff
kind: design
tags: staff, hr, time off, roll call, roster, menu
status: built

Main menu, Staff: the team roster is the starting point of the app. Staff holds HR requests, time off and roll
call. New businesses start with only the owner's own account, no test data.

## design.inventory_warehouse
kind: design
tags: inventory, warehouse, retail, stock house, upc, floor
status: built

Inventory Management, Warehouse (also called Stock house): retail stock by UPC, what's on the floor versus
what's waiting to be stocked, reconciled to invoices.

## design.inventory_stew
kind: design
tags: inventory, stew, recipe, kitchen, restaurant, costing
status: built

Inventory Management, Stew: recipe ingredient costing. Sales deplete inventory through the recipes. Order notes
are checked for hidden-ingredient conflicts (an allergy against an ingredient inside a sauce, for example).
Ordering comes from sales, waste and inventory.

## design.inventory_lab
kind: design
tags: inventory, lab, construction, phases, inspections, subcontractors, production
status: built

Inventory Management, Lab: long jobs like house builds or production lines, with phases, inspections,
subcontractors, reorder points and payroll per job.

## design.logo_switch_business
kind: design
tags: logo, switch business, demo, trial, business profile, menu
status: designed

Tapping the Human logo opens the switch-business menu: your business, the trial demos (Stock house, Stew, Lab,
Portfolio), the demo scene, and Business profile.

## design.hiwr_half_circle
kind: design
tags: hiwr, half circle, scan, import, recall, mesh, add, settings
status: designed

The HiWR half circle has six options. Scan (barcodes and documents). Import (CSV, PDF, PNG). Recall (recurring
numbers and averages). Mesh (staff organizer and team chat, who makes what). Add (quick commands, and Set up
role with training). Settings.

## design.settings_dial
kind: design
tags: settings, dial, refresh, end of day, disclaimers, about us
status: designed

Settings has a 1 to 10 falling-numbers dial, the end-of-business-day refresh, and draft disclaimers and About us.

## design.rpm
kind: design
tags: rpm, throughput, schedule, line, analytics
status: designed

RPM is per-line throughput, in that line's own unit (covers, units, orders), shown with the next client on the
schedule.

## design.appointment_ledger
kind: design
tags: appointments, late fees, no-show, ledger, reminders
status: designed

Appointment ledgers carry late and no-show fees. An alert goes out one business day before. Any fee or waiver
needs two approvers.

## design.business_pulse
kind: design
tags: pulse, analytics, today, wins, net gain
status: planned

The daily business pulse compares today with a normal day at the same hour and gives one level: Great, Good,
Normal, Slow or Rough, in one line by role. Wins are real facts only. Monthly net gain is income minus
expenses against the 3-month average, in HiWR analytics, split by role.

## design.assistant
kind: design
tags: assistant, chat, local, cloud, hybrid
status: built

The assistant runs locally on Ollama, in the cloud on Claude, or hybrid: routine lookups local, heavy planning
and pattern work in the cloud, falling back to local if the cloud is down. The cloud only sees labels, never
real names.

## design.signin
kind: design
tags: sign in, authenticator, employee id, setup code
status: built

Sign-in uses an authenticator app (TOTP) with a setup code from the owner. Employee ID details are taken in
encrypted and shown only at the right level.
