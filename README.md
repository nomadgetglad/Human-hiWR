# Human-hiWR

HiWR started as an app matching employees to employers through swipe-based profiles. It is growing into
an AI business-management system for local businesses: client records, scheduling, staffing and hiring,
inventory (Warehouse, Stew, Lab), customer service and money actions that always wait for human approval.

- [`hiwr/`](hiwr/): working sample code (Python 3 + SQLite, standard library only) with runnable demos.
  See [`hiwr/README.md`](hiwr/README.md).
- [`ui/`](ui/): the Human app screens (FastAPI + React PWA). See [`ui/README.md`](ui/README.md).
- [`billing/`](billing/): how Human Software charges its customers with Stripe (setup fee, monthly plan,
  add-ons, Stripe Tax, refunds behind two approvals). Test mode only. See [`billing/README.md`](billing/README.md).
- [`tests/`](tests/): `python -m pytest tests`. Security results and the signed-approval plan are in
  [`APPROVALS_SECURITY.md`](APPROVALS_SECURITY.md).
