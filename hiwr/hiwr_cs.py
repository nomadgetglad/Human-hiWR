"""Customer Service: the link between the customer and the order data.

Every question, answer and change points at a specific order line: its UPC, unit price, quantity and
specifications (modifiers, allergies, sizes, measurements). Nothing on a line changes without a
confirmation, and every change is logged with who asked and who confirmed.

Conflicts are asked, not assumed. "No tomato" on a burger whose house sauce contains ketchup:
  - a preference -> the line waits and the customer or server is asked: keep the sauce, leave it off,
    or swap. The kitchen doesn't fire that line until it's answered.
  - a declared allergy -> the component is held right away (safe default) and the guest still confirms.
Customer changes ("add a side of fries") become a draft with the new price, confirmed before they apply.
Once a line is past the stage where it can change (fired, or cut for made-to-order boots), the change
needs the kitchen or owner and may carry a remake cost.
Status texts go only with SMS consent.
"""
import json
from hiwr_stew import explode, contains, _is_recipe, _lines
from hiwr_contacts import can_text

def usd(c): return f"${c / 100:,.2f}"
LOCK_STAGE = {"restaurant_live": "fired", "boots_made_to_order": "cut"}


def say(db, order_id, who, channel, text, at):
    db.execute("INSERT INTO cs_messages(order_id, who, channel, text, at) VALUES (?,?,?,?,?)", (order_id, who, channel, text, at))
    return f"[{who}/{channel}] {text}"


def _change(db, order_id, item_id, field, old, new, by, at, confirmed_by=None):
    db.execute("INSERT INTO order_line_changes(order_item_id, order_id, field, old, new, requested_by, confirmed_by, at) VALUES (?,?,?,?,?,?,?,?)",
               (item_id, order_id, field, json.dumps(old) if old is not None else None, json.dumps(new) if new is not None else None, by, confirmed_by, at))


def add_line(db, order_id, upc, qty, specs, by, at):
    c = db.execute("SELECT * FROM catalog WHERE upc=?", (upc,)).fetchone()
    iid = db.execute("INSERT INTO order_items(order_id, item, qty, specs) VALUES (?,?,?,?)",
                     (order_id, c["item"], qty, json.dumps(specs or {}))).lastrowid
    db.execute("INSERT INTO order_line_meta(order_item_id, upc, unit_price_cents) VALUES (?,?,?)", (iid, upc, c["price_cents"]))
    _change(db, order_id, iid, "added", None, {"upc": upc, "qty": qty, "specs": specs}, by, at, confirmed_by=by)
    return iid


def _components_with(db, dish, tag):
    """Which direct components of the dish carry the tag (a sub-recipe or a raw ingredient)."""
    out = []
    for l in _lines(db, dish):
        if _is_recipe(db, l["component"]):
            if contains(db, l["component"], tag):
                out.append(l["component"])
        elif db.execute("SELECT 1 FROM material_allergens WHERE material=? AND tag=?", (l["component"], tag)).fetchone():
            out.append(l["component"])
    return out


def check_line(db, item_id, at, channel="table"):
    """Look for conflicts between the line's specs and what's really in the dish. Returns questions asked."""
    it = db.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()
    specs = json.loads(it["specs"] or "{}")
    asked = []
    for tag in specs.get("allergy", []):
        comps = _components_with(db, it["item"], tag)
        if comps:
            specs.setdefault("hold", [])
            specs["hold"] = sorted(set(specs["hold"]) | set(comps))
            db.execute("UPDATE order_items SET specs=? WHERE id=?", (json.dumps(specs), item_id))
            db.execute("UPDATE order_line_meta SET status='held_component' WHERE order_item_id=?", (item_id,))
            _change(db, it["order_id"], item_id, "hold", None, comps, "hiwr (allergy safe default)", at)
            asked.append(say(db, it["order_id"], "hiwr", channel,
                             f"ALLERGY ({tag}): {it['item']} has {tag} in {', '.join(comps)}. We're holding "
                             f"{', '.join(comps)} for safety. Can you confirm with the guest that this is right?", at))
    for tag in specs.get("no", []):
        if tag in specs.get("allergy", []):
            continue
        comps = _components_with(db, it["item"], tag)
        if comps:
            db.execute("UPDATE order_line_meta SET status='awaiting_answer' WHERE order_item_id=?", (item_id,))
            hidden = [m for m, (q, u, via) in explode(db, it["item"]).items()
                      if via and db.execute("SELECT 1 FROM material_allergens WHERE material=? AND tag=?", (m, tag)).fetchone()]
            q = (f"The {it['item']} comes with {comps[0].lower()}, which has {', '.join(hidden) or tag} in it. "
                 f"Did you want {', '.join(hidden) or comps[0]} on that, or should we leave the {comps[0].lower()} off?"
                 " (Or swap it for mayo.)")
            asked.append(say(db, it["order_id"], "hiwr", channel, q, at))
    return asked


def answer(db, item_id, choice, by, at, component, tag=None):
    """keep | leave_off | swap:<x>. Records it on the line and releases it to the kitchen."""
    it = db.execute("SELECT * FROM order_items WHERE id=?", (item_id,)).fetchone()
    specs = json.loads(it["specs"] or "{}")
    old = dict(specs)
    if choice == "keep":
        specs["confirmed_keep"] = sorted(set(specs.get("confirmed_keep", [])) | {component})
    elif choice == "leave_off":
        specs["hold"] = sorted(set(specs.get("hold", [])) | {component})
    elif choice.startswith("swap:"):
        specs["hold"] = sorted(set(specs.get("hold", [])) | {component})
        specs["add"] = sorted(set(specs.get("add", [])) | {choice[5:]})
    if tag:
        specs["no"] = [t for t in specs.get("no", []) if t != tag]   # the question is settled
        specs.setdefault("answered", []).append(f"{tag}: {choice} {component}")
    db.execute("UPDATE order_items SET specs=? WHERE id=?", (json.dumps(specs), item_id))
    db.execute("UPDATE order_line_meta SET status='ok' WHERE order_item_id=?", (item_id,))
    _change(db, it["order_id"], item_id, "specs", old, specs, by, at, confirmed_by=by)
    return specs


def _current_stage(db, order_id):
    r = db.execute("SELECT stage FROM order_stage_events WHERE order_id=? ORDER BY at DESC, id DESC LIMIT 1", (order_id,)).fetchone()
    return r["stage"] if r else None


def _locked(db, order_id):
    wf = db.execute("SELECT w.name FROM orders o JOIN workflows w ON w.id=o.workflow_id WHERE o.id=?", (order_id,)).fetchone()["name"]
    from hiwr_contacts import WORKFLOWS
    stages = [s for s, _ in WORKFLOWS[wf]]
    cur = _current_stage(db, order_id)
    lock = LOCK_STAGE.get(wf)
    return bool(lock and cur in stages and stages.index(cur) >= stages.index(lock)), cur


def propose_change(db, order_id, by, at, add=None, set_qty=None, set_spec=None):
    """A draft the customer confirms: what changes, on which line, and the new total."""
    locked, stage = _locked(db, order_id)
    lines = []
    if add:
        c = db.execute("SELECT * FROM catalog WHERE upc=?", (add["upc"],)).fetchone()
        lines.append(f"add {add['qty']} x {c['item']} (UPC {c['upc']}) at {usd(c['price_cents'])}")
    if set_qty:
        it = db.execute("SELECT * FROM order_items WHERE id=?", (set_qty[0],)).fetchone()
        lines.append(f"change {it['item']} qty {it['qty']} -> {set_qty[1]}")
    if set_spec:
        it = db.execute("SELECT * FROM order_items WHERE id=?", (set_spec[0],)).fetchone()
        lines.append(f"change {it['item']} {set_spec[1]}: {json.loads(it['specs']).get(set_spec[1])} -> {set_spec[2]}")
    new_total = order_total(db, order_id) + (db.execute("SELECT price_cents FROM catalog WHERE upc=?", (add["upc"],)).fetchone()[0] * add["qty"] if add else 0)
    if set_qty:
        m = db.execute("SELECT unit_price_cents FROM order_line_meta WHERE order_item_id=?", (set_qty[0],)).fetchone()[0]
        it = db.execute("SELECT qty FROM order_items WHERE id=?", (set_qty[0],)).fetchone()[0]
        new_total += m * (set_qty[1] - it)
    return {"order": order_id, "lines": lines, "new_total": new_total, "locked": locked, "stage": stage,
            "needs": "owner or kitchen OK (past " + stage + ")" if locked else "customer YES"}


def apply_change(db, order_id, draft, by, confirmed_by, at, add=None, set_qty=None, set_spec=None):
    if draft["locked"] and confirmed_by not in ("owner_alex", "chef_marco", "maker_dee"):
        raise PermissionError(f"order is past {draft['stage']}; needs the owner or kitchen to approve")
    if add:
        add_line(db, order_id, add["upc"], add["qty"], add.get("specs"), by, at)
    if set_qty:
        it = db.execute("SELECT * FROM order_items WHERE id=?", (set_qty[0],)).fetchone()
        db.execute("UPDATE order_items SET qty=? WHERE id=?", (set_qty[1], set_qty[0]))
        _change(db, order_id, set_qty[0], "qty", it["qty"], set_qty[1], by, at, confirmed_by)
    if set_spec:
        it = db.execute("SELECT * FROM order_items WHERE id=?", (set_spec[0],)).fetchone()
        s = json.loads(it["specs"]); old = dict(s); s[set_spec[1]] = set_spec[2]
        db.execute("UPDATE order_items SET specs=? WHERE id=?", (json.dumps(s), set_spec[0]))
        _change(db, order_id, set_spec[0], "specs", old, s, by, at, confirmed_by)
    return order_total(db, order_id)


def order_total(db, order_id):
    return db.execute("SELECT COALESCE(SUM(i.qty * m.unit_price_cents),0) FROM order_items i JOIN order_line_meta m ON m.order_item_id=i.id "
                      "WHERE i.order_id=?", (order_id,)).fetchone()[0]


def kitchen_ticket(db, order_id):
    out = []
    for i in db.execute("SELECT i.*, m.status FROM order_items i JOIN order_line_meta m ON m.order_item_id=i.id WHERE i.order_id=?", (order_id,)):
        s = json.loads(i["specs"] or "{}")
        notes = [f"HOLD {h}" for h in s.get("hold", [])] + [f"ADD {a}" for a in s.get("add", [])] + \
                [f"ALLERGY {a}" for a in s.get("allergy", [])] + [f"{k} {v}" for k, v in s.items() if k not in ("hold", "add", "allergy", "no", "confirmed_keep", "answered")]
        out.append(f"{i['qty']} x {i['item']}" + (f"  [{'; '.join(notes)}]" if notes else "") +
                   ("  WAIT: question to guest" if i["status"] == "awaiting_answer" else ""))
    return out


def sell_order(db, order_id, at):
    """Deplete Stew inventory for the order, skipping held components."""
    from hiwr_stew import explode
    for i in db.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)):
        if not _is_recipe(db, i["item"]):
            continue
        holds = set(json.loads(i["specs"] or "{}").get("hold", []))
        for l in _lines(db, i["item"]):
            if l["component"] in holds:
                continue
            parts = explode(db, l["component"], l["qty"] / db.execute("SELECT yield_qty FROM recipes WHERE name=?", (l["component"],)).fetchone()[0]) \
                if _is_recipe(db, l["component"]) else {l["component"]: (l["qty"], l["unit"], [])}
            for m, (q, u, via) in parts.items():
                db.execute("UPDATE materials SET on_hand=on_hand-? WHERE material=?", (q * i["qty"], m))
                db.execute("INSERT INTO material_moves(material, qty, reason, ref, at) VALUES (?,?,?,?,?)",
                           (m, -q * i["qty"], f"order {order_id} {i['item']}", str(order_id), at))


def status_update(db, order_id, text, at):
    o = db.execute("SELECT o.*, p.e164 FROM orders o LEFT JOIN contact_phones p ON p.contact_id=o.contact_id AND p.is_primary=1 WHERE o.id=?",
                   (order_id,)).fetchone()
    if o["e164"] and can_text(db, o["e164"]):
        return say(db, order_id, "hiwr", "sms", text, at)
    return say(db, order_id, "hiwr", "call list", text, at)
