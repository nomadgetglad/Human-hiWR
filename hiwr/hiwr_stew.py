"""Inventory Management > Stew: recipe costing and live ingredient counts.

  cost      each dish's cost per plate from its ingredients, including sub-recipes (salsa inside a taco),
            at the preferred vendor's current price; food cost % against the menu price
  hidden    every ingredient a dish contains, sub-recipes included, with allergen tags, so an order
            note like "no tomato" is checked against the salsa inside it
  sell      each dish sold depletes its ingredients from inventory (append-only moves)
  portions  how many of each dish the current stock can still make; a short delivery cuts it at once,
            and dishes about to run out are flagged to 86
Costs are Manager level; line cooks see portions left and allergens, not prices.
"""
import math

def _lines(db, recipe):
    return db.execute("SELECT * FROM recipe_lines WHERE recipe=?", (recipe,)).fetchall()


def _is_recipe(db, name):
    return db.execute("SELECT 1 FROM recipes WHERE name=?", (name,)).fetchone() is not None


def explode(db, recipe, mult=1.0, path=None):
    """Raw materials per one unit of recipe, walking sub-recipes. Returns {material: (qty, unit, via)}."""
    out = {}
    for l in _lines(db, recipe):
        if _is_recipe(db, l["component"]):
            sub = db.execute("SELECT * FROM recipes WHERE name=?", (l["component"],)).fetchone()
            for m, (q, u, via) in explode(db, l["component"], mult * l["qty"] / sub["yield_qty"],
                                         (path or []) + [l["component"]]).items():
                prev = out.get(m, (0, u, via))
                out[m] = (prev[0] + q, u, via)
        else:
            prev = out.get(l["component"], (0, l["unit"], path or []))
            out[l["component"]] = (prev[0] + l["qty"] * mult, l["unit"], path or [])
    return out


def unit_cost(db, material):
    r = db.execute("SELECT unit_cost_cents FROM material_sources WHERE material=? ORDER BY preferred DESC, unit_cost_cents LIMIT 1",
                   (material,)).fetchone()
    return r["unit_cost_cents"] if r else 0


def cost_card(db, dish):
    d = db.execute("SELECT * FROM recipes WHERE name=?", (dish,)).fetchone()
    rows, total = [], 0.0
    for m, (q, u, via) in sorted(explode(db, dish).items()):
        c = q * unit_cost(db, m)
        total += c
        rows.append({"material": m, "qty": round(q, 3), "unit": u, "cents": round(c, 1), "via": " > ".join(via) or "direct"})
    return {"dish": dish, "price": d["price_cents"], "cost": round(total), "food_cost_pct": round(100 * total / d["price_cents"], 1),
            "lines": rows}


def contains(db, dish, tag):
    hits = []
    for m, (q, u, via) in explode(db, dish).items():
        if db.execute("SELECT 1 FROM material_allergens WHERE material=? AND tag=?", (m, tag)).fetchone():
            hits.append(f"{m} (in {' > '.join(via)})" if via else m)
    return hits


def check_order_note(db, dish, note):
    """'no tomato' -> every place tomato hides in the dish, so the line can pull or swap it."""
    words = note.lower().replace("no ", "").strip()
    hits = contains(db, dish, words)
    if not hits:
        return f"OK: {dish} has no {words}"
    return f"CONFLICT: {dish} contains {words} in " + ", ".join(hits) + ". Confirm with the guest or swap before firing."


def portions_left(db):
    out = []
    for d in db.execute("SELECT name FROM recipes WHERE kind='dish'"):
        need = explode(db, d["name"])
        limits = []
        for m, (q, u, via) in need.items():
            on = db.execute("SELECT on_hand FROM materials WHERE material=?", (m,)).fetchone()["on_hand"]
            limits.append((math.floor(on / q) if q else 10**9, m))
        n, limiting = min(limits)
        out.append({"dish": d["name"], "portions": n, "limited_by": limiting})
    return sorted(out, key=lambda r: r["portions"])


def sell(db, dish, qty, at, ref):
    for m, (q, u, via) in explode(db, dish).items():
        db.execute("UPDATE materials SET on_hand=on_hand-? WHERE material=?", (q * qty, m))
        db.execute("INSERT INTO material_moves(material, qty, reason, ref, at) VALUES (?,?,?,?,?)", (m, -q * qty, f"sold {qty} {dish}", ref, at))


def receive_short(db, material, expected, received, at, ref):
    """Only what arrived goes into inventory; the shortfall belongs on a SHIPMENT ticket."""
    db.execute("UPDATE materials SET on_hand=on_hand+? WHERE material=?", (received, material))
    db.execute("INSERT INTO material_moves(material, qty, reason, ref, at) VALUES (?,?,?,?,?)",
               (material, received, f"received {received} of {expected}", ref, at))
