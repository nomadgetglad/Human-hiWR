"""Manager ordering: reorder lists built ahead of time from predicted use, with vendor UPCs,
plus suggestions for using back stock before buying more.

Predicted use    = same-weekday average dish sales (last 4 weeks) for each coming day, exploded through
                   Stew recipes into ingredients.
Order quantity   = use from now until the next order arrives + safety days - on hand,
                   rounded up to whole cases of the vendor's UPC.
Order-by date    = the day stock would drop to the safety level, minus the vendor's lead time.
Lines group into one PO draft per vendor; a manager reviews and sends. Open SHIPMENT tickets for a
vendor are shown on its PO.
Back stock       = more on hand than will be used before it expires, or over 21 days of supply.
                   Suggestions: skip or cut the reorder (done automatically), a Stew special that uses it,
                   a Warehouse bundle or sale for retail items, a transfer to another location that needs it,
                   or a donation before the date. The manager picks; the pick is logged.
"""
import math
from datetime import date, timedelta
from hiwr_stew import explode, cost_card
from hiwr_access import level

def _d(s): return date.fromisoformat(s)
def usd(c): return f"${c / 100:,.2f}"


def daily_dish_forecast(db, day, weeks=4):
    past = [(_d(day) - timedelta(weeks=w)).isoformat() for w in range(1, weeks + 1)]
    out = {}
    for r in db.execute("SELECT dish, SUM(qty) q FROM dish_sales WHERE day IN (%s) GROUP BY dish" % ",".join("?" * len(past)), past):
        out[r["dish"]] = r["q"] / weeks
    return out


def material_forecast(db, start, days):
    use = {}
    for i in range(days):
        day = (_d(start) + timedelta(days=i)).isoformat()
        for dish, n in daily_dish_forecast(db, day).items():
            if not db.execute("SELECT 1 FROM recipes WHERE name=? AND kind='dish'", (dish,)).fetchone():
                continue
            for m, (q, u, via) in explode(db, dish).items():
                use.setdefault(m, [0.0] * days)[i] += q * n
    return use


def plan_orders(db, today, horizon=21, made_by="hiwr"):
    use = material_forecast(db, today, horizon)
    by_vendor, back = {}, []
    for m, daily in sorted(use.items()):
        mat = db.execute("SELECT * FROM materials WHERE material=?", (m,)).fetchone()
        par = db.execute("SELECT * FROM material_par WHERE material=?", (m,)).fetchone()
        src = db.execute("SELECT s.*, v.upc, v.pack_size, v.pack_label FROM material_sources s JOIN vendor_items v "
                         "ON v.material=s.material AND v.vendor=s.vendor WHERE s.material=? ORDER BY s.preferred DESC LIMIT 1", (m,)).fetchone()
        avg = sum(daily) / len(daily)
        on = mat["on_hand"]
        safety = par["safety_days"] * avg
        # day stock hits safety
        left, hit = on, None
        for i, u in enumerate(daily):
            left -= u
            if left <= safety and hit is None:
                hit = i
        dos = on / avg if avg else float("inf")
        to_exp = (_d(par["expires_on"]) - _d(today)).days if par["expires_on"] else None
        use_before_exp = sum(daily[:to_exp]) if to_exp is not None else on
        excess = max(0, on - use_before_exp) if to_exp is not None else max(0, on - 21 * avg)
        if excess > 0 or dos > 21:
            back.append({"material": m, "unit": mat["unit"], "on_hand": on, "days_of_supply": round(dos, 1),
                         "expires_in": to_exp, "excess": round(excess, 1), "avg_use": round(avg, 1)})
        if hit is None or not src:
            continue
        order_by = _d(today) + timedelta(days=max(0, hit - src["lead_days"]))
        arrive = order_by + timedelta(days=src["lead_days"])
        cover_days = src["lead_days"] + par["review_days"]
        need = sum(daily[:min(horizon, (arrive - _d(today)).days + par["review_days"])]) + safety - on
        if need <= 0:
            continue
        packs = math.ceil(need / src["pack_size"])
        line = {"material": m, "upc": src["upc"], "packs": packs, "pack": src["pack_label"], "units": packs * src["pack_size"],
                "cost": round(packs * src["pack_size"] * src["unit_cost_cents"]), "order_by": order_by.isoformat(),
                "why": f"uses {avg:.1f} {mat['unit']}/day, {on:g} on hand, hits safety in {hit} days"}
        by_vendor.setdefault(src["vendor"], []).append(line)
    pos = []
    for v, lines in by_vendor.items():
        ob = min(l["order_by"] for l in lines)
        lead = db.execute("SELECT MAX(lead_days) FROM material_sources WHERE vendor=?", (v,)).fetchone()[0]
        pid = db.execute("INSERT INTO po_drafts(vendor, order_by, delivery_on, made_by) VALUES (?,?,?,?)",
                         (v, ob, (_d(ob) + timedelta(days=lead)).isoformat(), made_by)).lastrowid
        for l in lines:
            db.execute("INSERT INTO po_lines VALUES (?,?,?,?,?,?,?)", (pid, l["material"], l["upc"], l["packs"], l["units"], l["cost"], l["why"]))
        open_t = [t["id"] for t in db.execute("SELECT id FROM tickets WHERE type='SHIPMENT' AND status!='closed' AND subject LIKE ?", (f"%{v}%",))]
        pos.append({"po": pid, "vendor": v, "order_by": ob, "lines": lines, "total": sum(l["cost"] for l in lines), "open_tickets": open_t})
    return pos, back


def backstock_suggestions(db, item, today, other_locations=None):
    m = item["material"]
    out = [f"Skip or cut the next reorder of {m} (already left off the PO drafts)"]
    dishes = []
    for d in db.execute("SELECT name, price_cents FROM recipes WHERE kind='dish'"):
        per = explode(db, d["name"]).get(m)
        if per:
            dishes.append((per[0], d["name"], d["price_cents"]))
    dishes.sort(reverse=True)
    if dishes and item["excess"]:
        per, name, price = dishes[0]
        days = item["expires_in"] or 7
        extra = math.ceil(item["excess"] / per / max(1, days))
        out.append(f"Stew special: feature {name} (uses {per:g} {item['unit']} each); about {extra} extra a day for {days} days uses the excess."
                   f" Food cost {cost_card(db, name)['food_cost_pct']}% so a $2 discount still leaves margin")
    if db.execute("SELECT 1 FROM products WHERE name LIKE ?", (f"%{m}%",)).fetchone():
        out.append(f"Warehouse: bundle or mark down the retail {m} (see sale proposals)")
    for loc, needs in (other_locations or {}).items():
        if m in needs:
            out.append(f"Transfer {min(item['excess'], needs[m]):g} {item['unit']} to {loc}, which is short")
    if item["expires_in"] is not None:
        out.append(f"Donate the excess to a food bank by {(_d(today) + timedelta(days=max(0, item['expires_in'] - 1))).isoformat()}"
                   " (good-faith food donations are generally protected under the federal Bill Emerson Good Samaritan Act)")
    return out


def choose(db, material, choice, detail, member, at):
    if level(db, member) < 2:
        raise PermissionError(f"{member} can't decide on stock use (needs Manager level)")
    db.execute("INSERT INTO backstock_choices(material, choice, detail, chosen_by, at) VALUES (?,?,?,?,?)", (material, choice, detail, member, at))


def send_po(db, po_id, member):
    if level(db, member) < 2:
        raise PermissionError(f"{member} can't send purchase orders (needs Manager level)")
    db.execute("UPDATE po_drafts SET status='sent', sent_by=? WHERE id=?", (member, po_id))
