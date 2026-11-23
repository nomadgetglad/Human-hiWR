"""The contract between HiWR (data) and Human (the AI work model).

HiWR provides QUANTITIES: what happened, measured. Human provides OPTIONS and the NEEDED QUANTITIES for each
(hours, dollars, units, percent). Both sides are built against the same version of this file, so a change to
one is a change to both: Human refuses a snapshot from a different contract version instead of guessing.
"""
CONTRACT_VERSION = "1.0"

# every quantity HiWR puts in a snapshot: name -> unit
SNAPSHOT_FIELDS = {
    "window": "dates", "net_sales": "cents", "food_sales": "cents", "beverage_sales": "cents",
    "food_cost": "cents", "beverage_cost": "cents", "labor": "cents", "direct_labor": "cents",
    "shared_labor": "cents", "occupancy": "cents", "operating": "cents", "waste": "cents",
    "food_cost_pct": "pct", "beverage_cost_pct": "pct", "labor_pct": "pct", "prime_cost_pct": "pct",
    "operating_margin_pct": "pct", "labor_hours": "hours", "avg_wage": "cents/hour",
    "drinks_sold": "count", "avg_drink_price": "cents", "plates_sold": "count", "avg_plate_price": "cents",
    "food_vendor_share_pct": "pct by vendor", "unconfirmed_cents": "cents",
}
# every needed quantity Human may put on an option: name -> unit
NEED_FIELDS = {
    "recover": "cents", "hours_to_move": "hours", "waste_to_avoid": "cents", "extra_drinks": "count",
    "price_change_pct": "pct", "per_plate_increase": "cents", "target_pct": "pct",
}


def check_snapshot(snap):
    if snap.get("contract") != CONTRACT_VERSION:
        raise ValueError(f"snapshot is contract {snap.get('contract')}, Human is built for {CONTRACT_VERSION}: "
                         "rebuild both together")
    missing = [f for f in SNAPSHOT_FIELDS if f not in snap]
    extra = [f for f in snap if f not in SNAPSHOT_FIELDS and f != "contract"]
    if missing or extra:
        raise ValueError(f"snapshot doesn't match contract {CONTRACT_VERSION}: missing {missing}, unknown {extra}")


def check_option(opt):
    bad = [k for k in opt["needs"] if k not in NEED_FIELDS]
    if bad:
        raise ValueError(f"option {opt['name']} uses needs not in the contract: {bad}")
