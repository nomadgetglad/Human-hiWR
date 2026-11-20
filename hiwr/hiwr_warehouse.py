"""Inventory Management > Warehouse: what the stocker sees.

For each item: what's on the floor, what's in the back, the shelf par and capacity. The pull list says
what to bring out (up to capacity, from back stock, oldest dates first), in aisle order, and what is out
everywhere and needs ordering. Stockers (level 0) see units and locations only, never costs.
The sale and markdown proposals for slow movers (hiwr_retail.py) also live in Warehouse, for managers.
"""

def pull_list(db, today):
    out = []
    for p in db.execute("SELECT p.*, s.aisle, s.capacity, s.par FROM products p JOIN shelf_plan s ON s.sku=p.sku ORDER BY s.aisle, p.name"):
        if p["shelf"] >= p["par"]:
            continue
        room = p["capacity"] - p["shelf"]
        pull = min(room, p["back_stock"])
        if p["shelf"] == 0 and pull:
            status = "OUT ON FLOOR - pull now"
        elif p["shelf"] == 0:
            status = "OUT EVERYWHERE - tell manager to order"
        elif pull:
            status = "below par"
        else:
            status = "low, no back stock - order"
        out.append({"aisle": p["aisle"], "sku": p["sku"], "item": p["name"], "floor": p["shelf"], "par": p["par"],
                    "back": p["back_stock"], "pull": pull, "status": status,
                    "rotate": "pull oldest date forward" if p["expires_on"] else ""})
    rank = {"OUT ON FLOOR - pull now": 0, "OUT EVERYWHERE - tell manager to order": 1, "below par": 2, "low, no back stock - order": 3}
    out.sort(key=lambda r: (rank[r["status"]], r["aisle"]))
    return out


def record_pull(db, sku, qty, member, at):
    p = db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    if qty > p["back_stock"]:
        raise ValueError(f"only {p['back_stock']} in back stock")
    db.execute("UPDATE products SET shelf=shelf+?, back_stock=back_stock-? WHERE sku=?", (qty, qty, sku))
    db.execute("INSERT INTO stock_moves(sku, qty, from_loc, to_loc, member, at) VALUES (?,?, 'back', 'floor', ?, ?)",
               (sku, qty, member, at))
