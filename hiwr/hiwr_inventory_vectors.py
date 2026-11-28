"""Vectors for UPC and SKU inventory: one row of numbers per item, and a name embedding per item.

Two kinds of vector, used for different jobs:

1. Stock vectors (numbers you can do maths on). One fixed list of features per SKU or Stew ingredient:
   floor, back, on hand, sales per day over 7 and 28 days, days of supply, days to the nearest expiry, units
   likely to expire unsold, open and held lots, and (Manager level and up only) cost, price and margin.
   Every item has the same columns in the same order, so the whole inventory is one table of numbers
   (a matrix). Stockout and reorder checks run on every row at once, and the same matrix can be handed to
   hiwr_ml / PyTorch as a tensor without reshaping: torch.tensor(m["rows"]).

2. Name embeddings (meaning, for search). Each item name becomes a vector, so "tortilla chips 13 oz"
   lands near "Tortilla Chips 13oz". Used only to SUGGEST: which existing item an unknown barcode might be,
   and which two SKUs might be duplicates. A person picks. A UPC/GTIN is always an exact key and is never
   matched by similarity (see hiwr_barcodes.py).

The default embedder is built in (standard library, letter-trigram hashing). Any embedder with
embed(list_of_texts) -> list_of_vectors can be passed instead, e.g. the Ollama nomic-embed-text one in
agent/vectors.py. Every reorder or duplicate finding is written as an inventory proposal; nothing is ordered,
merged or changed until a manager accepts it.
"""
import hashlib
import json
import math
import re
from datetime import date, timedelta

from hiwr_access import can_see, level

BASE_FEATURES = ["floor", "back", "on_hand", "per_day_7", "per_day_28", "days_of_supply",
                 "days_to_expiry", "expiring_unsold", "open_lots", "held_lots"]
COST_FEATURES = ["cost_cents", "price_cents", "margin_pct"]
NO_EXPIRY = 9999.0          # stands in for "never" so every cell stays a number
NO_SALES = 9999.0           # days of supply when nothing sells


def _d(s): return date.fromisoformat(s[:10])


# ---------------------------------------------------------------- stock vectors

def _lots(db, kind, ref, today):
    from hiwr_barcodes import lot_status
    rows = db.execute("SELECT id, expires_on FROM lots WHERE item_kind=? AND item_ref=?", (kind, ref)).fetchall()
    held = sum(lot_status(db, r["id"]) in ("on_hold", "recalled") for r in rows)
    live = [r for r in rows if lot_status(db, r["id"]) == "ok"]
    exp = min((r["expires_on"] for r in live if r["expires_on"]), default=None)
    return len(live), held, exp


def product_vector(db, sku, today, with_costs=False):
    p = db.execute("SELECT * FROM products WHERE sku=?", (sku,)).fetchone()
    t = _d(today)

    def sold(days):
        start = (t - timedelta(days=days)).isoformat()
        return db.execute("SELECT COALESCE(SUM(units),0) FROM daily_sales WHERE sku=? AND day>=? AND day<?",
                          (sku, start, today)).fetchone()[0] / days

    v7, v28 = sold(7), sold(28)
    on_hand = p["shelf"] + p["back_stock"]
    open_lots, held, lot_exp = _lots(db, "product", sku, today)
    exp = lot_exp or p["expires_on"]
    to_exp = float((_d(exp) - t).days) if exp else NO_EXPIRY
    rate = max(v7, v28)                                    # the busier recent rate, so stockouts aren't missed
    vec = {"floor": p["shelf"], "back": p["back_stock"], "on_hand": on_hand, "per_day_7": round(v7, 2),
           "per_day_28": round(v28, 2), "days_of_supply": round(on_hand / rate, 1) if rate else NO_SALES,
           "days_to_expiry": to_exp,
           "expiring_unsold": max(0.0, round(on_hand - v28 * to_exp)) if exp else 0.0,
           "open_lots": open_lots, "held_lots": held}
    if with_costs:
        vec.update(cost_cents=p["cost_cents"], price_cents=p["price_cents"],
                   margin_pct=round(100 * (p["price_cents"] - p["cost_cents"]) / p["price_cents"], 1) if p["price_cents"] else 0.0)
    return vec


def material_vector(db, material, today, with_costs=False):
    """Stew ingredient: use per day comes from the append-only material_moves (negative qty = used)."""
    m = db.execute("SELECT * FROM materials WHERE material=?", (material,)).fetchone()
    t = _d(today)

    def used(days):
        start = (t - timedelta(days=days)).isoformat()
        q = db.execute("SELECT COALESCE(-SUM(qty),0) FROM material_moves WHERE material=? AND qty<0 AND at>=? AND at<?",
                       (material, start, today)).fetchone()[0]
        return q / days

    v7, v28 = used(7), used(28)
    rate = max(v7, v28)
    open_lots, held, exp = _lots(db, "material", material, today)
    to_exp = float((_d(exp) - t).days) if exp else NO_EXPIRY
    vec = {"floor": 0.0, "back": m["on_hand"], "on_hand": m["on_hand"], "per_day_7": round(v7, 2),
           "per_day_28": round(v28, 2), "days_of_supply": round(m["on_hand"] / rate, 1) if rate else NO_SALES,
           "days_to_expiry": to_exp,
           "expiring_unsold": max(0.0, round(m["on_hand"] - v28 * to_exp, 1)) if exp else 0.0,
           "open_lots": open_lots, "held_lots": held}
    if with_costs:
        src = db.execute("SELECT unit_cost_cents FROM material_sources WHERE material=? ORDER BY preferred DESC LIMIT 1",
                         (material,)).fetchone()
        vec.update(cost_cents=src["unit_cost_cents"] if src else 0, price_cents=0, margin_pct=0.0)
    return vec


def matrix(db, member, today, kind="product"):
    """The whole inventory as rows of numbers. Costs are columns only for Manager level (2) and up."""
    with_costs = can_see(db, member, "order_costs")
    cols = BASE_FEATURES + (COST_FEATURES if with_costs else [])
    if kind == "product":
        keys = [r[0] for r in db.execute("SELECT sku FROM products ORDER BY sku")]
        vecs = [product_vector(db, k, today, with_costs) for k in keys]
    else:
        keys = [r[0] for r in db.execute("SELECT material FROM materials ORDER BY material")]
        vecs = [material_vector(db, k, today, with_costs) for k in keys]
    return {"kind": kind, "columns": cols, "keys": keys, "rows": [[float(v[c]) for c in cols] for v in vecs]}


def column(m, name):
    i = m["columns"].index(name)
    return [r[i] for r in m["rows"]]


# ---------------------------------------------------------------- proposals from the matrix

def _case_size(db, kind, ref):
    r = db.execute("SELECT base_qty FROM barcodes WHERE item_kind=? AND item_ref=? AND pack_level='case' "
                   "ORDER BY added_at DESC LIMIT 1", (kind, ref)).fetchone()
    if r:
        return r["base_qty"]
    if kind == "material":
        v = db.execute("SELECT pack_size FROM vendor_items WHERE material=? LIMIT 1", (ref,)).fetchone()
        if v:
            return v["pack_size"]
    return 1


def _lead_days(db, kind, ref, default):
    if kind == "material":
        r = db.execute("SELECT lead_days FROM material_sources WHERE material=? ORDER BY preferred DESC LIMIT 1", (ref,)).fetchone()
        if r:
            return r["lead_days"]
    return default


def _propose(db, kind, ref, detail, at):
    db.execute("INSERT INTO inventory_proposals(kind, item_ref, detail, made_at) VALUES (?,?,?,?)",
               (kind, ref, json.dumps(detail, sort_keys=True), at))
    return db.execute("SELECT last_insert_rowid()").fetchone()[0]


def stock_proposals(db, member, today, kind="product", cover_days=7, safety_days=2, default_lead_days=3):
    """Reorder when days of supply won't last the lead time plus a safety margin. Order enough to cover
    lead time + cover_days, rounded up to whole cases. Expiry risk when units will likely expire unsold.
    cover_days, safety_days and default_lead_days are sample settings for the owner to set."""
    m = matrix(db, member, today, kind)
    idx = {c: i for i, c in enumerate(m["columns"])}
    out = []
    for key, row in zip(m["keys"], m["rows"]):
        rate = max(row[idx["per_day_7"]], row[idx["per_day_28"]])
        lead = _lead_days(db, kind, key, default_lead_days)
        dos = row[idx["days_of_supply"]]
        if rate and dos < lead + safety_days:
            need = rate * (lead + cover_days) - row[idx["on_hand"]]
            case = _case_size(db, kind, key)
            cases = max(1, math.ceil(need / case))
            detail = {"days_of_supply": dos, "lead_days": lead, "per_day": round(rate, 2), "on_hand": row[idx["on_hand"]],
                      "case_size": case, "cases": cases, "units": cases * case,
                      "why": f"{dos} days left, {lead} days to arrive"}
            out.append({"id": _propose(db, "reorder", key, detail, today), "kind": "reorder", "item": key, **detail})
        if row[idx["expiring_unsold"]] > 0 and row[idx["held_lots"]] == 0:
            detail = {"expiring_unsold": row[idx["expiring_unsold"]], "days_to_expiry": row[idx["days_to_expiry"]],
                      "why": "likely to expire before it sells; see the sale proposal in Warehouse"}
            out.append({"id": _propose(db, "expiry_risk", key, detail, today), "kind": "expiry_risk", "item": key, **detail})
    return out


def decide(db, proposal_id, decision, member, at):
    """A manager accepts or rejects. Accepting a reorder still only makes a purchase draft elsewhere;
    nothing is bought here."""
    if level(db, member) < 2:
        raise PermissionError("a manager (level 2+) decides inventory proposals")
    if db.execute("SELECT 1 FROM inventory_proposal_decisions WHERE proposal_id=?", (proposal_id,)).fetchone():
        raise ValueError("already decided")
    db.execute("INSERT INTO inventory_proposal_decisions VALUES (?,?,?,?)", (proposal_id, decision, member, at))


# ---------------------------------------------------------------- name embeddings

DIM = 256


def normalize(text):
    """Lowercase, split numbers from units (13oz -> 13 oz), drop punctuation."""
    t = text.lower()
    t = re.sub(r"(\d)([a-z])", r"\1 \2", t)
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)
    return " ".join(re.sub(r"[^a-z0-9.]+", " ", t).split())


def hash_embed(text):
    """Letter trigrams and whole words hashed into DIM slots, then scaled to length 1.
    Same text, same vector, on any computer, with no model to download."""
    v = [0.0] * DIM
    t = normalize(text)
    grams = [w for w in t.split()] + [f" {t} "[i:i + 3] for i in range(len(t))]
    for g in grams:
        h = int(hashlib.sha1(g.encode()).hexdigest()[:8], 16)
        v[h % DIM] += 1.0 if (h >> 8) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    def embed(self, texts):
        return [hash_embed(t) for t in texts]


def cosine(a, b):
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def _names(db):
    items = [("product", r["sku"], r["name"]) for r in db.execute("SELECT sku, name FROM products")]
    items += [("material", r["material"], r["material"]) for r in db.execute("SELECT material FROM materials")]
    return items


def similar_items(db, text, k=3, min_score=0.3, embedder=None):
    """Closest item names to what someone typed. Suggestions only."""
    embedder = embedder or HashEmbedder()
    items = _names(db)
    if not items:
        return []
    vecs = embedder.embed([text] + [n for _, _, n in items])
    scored = [(round(cosine(vecs[0], v), 3), kind, ref, name) for v, (kind, ref, name) in zip(vecs[1:], items)]
    scored.sort(key=lambda s: -s[0])
    return [{"score": s, "item_kind": kind, "item_ref": ref, "name": name} for s, kind, ref, name in scored[:k] if s >= min_score]


def suggest_for_unknown(db, raw_code, typed_name, at, embedder=None):
    """An unknown code was scanned and the person typed what it is. Record the closest existing items as a
    proposal. The code itself is only linked when a person picks one and a manager confirms it."""
    matches = similar_items(db, typed_name, embedder=embedder)
    pid = _propose(db, "unknown_code_match", raw_code, {"typed": typed_name, "candidates": matches}, at)
    return {"id": pid, "typed": typed_name, "candidates": matches}


def possible_duplicates(db, at, threshold=0.8, embedder=None):
    """Pairs of SKUs whose names are near-identical (two SKUs for one item splits its stock and sales)."""
    embedder = embedder or HashEmbedder()
    items = [i for i in _names(db) if i[0] == "product"]
    vecs = embedder.embed([n for _, _, n in items])
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            s = round(cosine(vecs[i], vecs[j]), 3)
            if s >= threshold:
                detail = {"other": items[j][1], "names": [items[i][2], items[j][2]], "score": s}
                out.append({"id": _propose(db, "possible_duplicate", items[i][1], detail, at), "a": items[i][1], "b": items[j][1], "score": s})
    return out
