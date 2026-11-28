"""Barcodes: turn a scan into an item, a count in base units and a lot. Exact keys only.

Steps for every scan (the Scan option in the HiWR circle):
  1. Validate the check digit. A bad read is rejected and logged; nothing moves.
  2. Recognise the shape: GS1-128 with Application Identifiers, a 2-prefix price/weight code, a GTIN.
  3. Normalise to GTIN-14, so a UPC-A and the EAN-13 of the same can are one key.
  4. Look the code up. An unknown code is never guessed: HiWR asks the person what it is and how many are
     inside, saves it as unconfirmed, and a manager (level 2+) confirms it once before it can change stock.
  5. Lot and expiry come from AIs 10 and 17. A lot-tracked item scanned without them asks for them.
Every scan is written to scan_log, even rejected ones. Codes are never matched by similarity:
hiwr_inventory_vectors.py may suggest which existing item an unknown code might be, and a person picks.
"""
import re
from datetime import date

from hiwr_access import level

# ---------------------------------------------------------------- check digits


def check_digit(body):
    """GS1 check digit for any length: from the right, weights 3, 1, 3, 1 ...
    check_digit("03600029145") == 2  ->  036000291452"""
    if not body.isdigit():
        raise ValueError("digits only")
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return (10 - total % 10) % 10


def is_valid(code):
    return code.isdigit() and len(code) in (8, 12, 13, 14, 18) and check_digit(code[:-1]) == int(code[-1])


def expand_upce(code):
    """8-digit UPC-E (number system, 6 digits, check) back to the 12-digit UPC-A it was squeezed from."""
    ns, d, chk = code[0], code[1:7], code[7]
    last = d[5]
    if last in "012":
        body = d[0:2] + last + "0000" + d[2:5]
    elif last == "3":
        body = d[0:3] + "00000" + d[3:5]
    elif last == "4":
        body = d[0:4] + "00000" + d[4]
    else:
        body = d[0:5] + "0000" + last
    return ns + body + chk


def to_gtin14(code):
    """Pad any GTIN to 14 digits. UPC-E is expanded first. Raises ValueError on a bad check digit."""
    code = code.strip()
    if len(code) == 8 and code[0] in "01" and _upce_ok(code):
        code = expand_upce(code)
    if not is_valid(code) or len(code) == 18:
        raise ValueError(f"bad check digit or length: {code}")
    return code.zfill(14)


def _upce_ok(code):
    try:
        return is_valid(expand_upce(code))
    except (IndexError, ValueError):
        return False


def symbology(code):
    return {12: "upc_a", 13: "ean13", 14: "itf14"}.get(len(code), "upc_e" if _upce_ok(code) else "ean8")


def with_indicator(upc12, indicator):
    """Case or inner-pack GTIN-14: indicator digit + the UPC without its check digit + a new check digit.
    with_indicator("036000291452", 1) == "10036000291459" """
    body = str(indicator) + upc12.zfill(13)[:-1]
    return body + str(check_digit(body))


def house_code(n):
    """Store-internal code for an item with no UPC: UPC-A starting with 4. house_code(1023) == '400000010236'."""
    body = "4" + str(n).zfill(10)
    return body + str(check_digit(body))


# ---------------------------------------------------------------- GS1-128

FNC1 = "\x1d"                      # what most scanners send for the invisible separator
FIXED = {"00": 18, "01": 14, "02": 14, "11": 6, "13": 6, "15": 6, "17": 6}
VARIABLE = {"10": 20, "21": 20}    # lot, serial: up to 20 characters, end at FNC1 or end of data
NAMES = {"00": "sscc", "01": "gtin", "02": "contained_gtin", "10": "lot", "11": "produced_on", "13": "packed_on",
         "15": "best_before", "17": "expires_on", "21": "serial"}


def _yymmdd(s):
    """GS1 dates: YY in 00-49 is 20YY, 50-99 is 19YY. Day 00 means the last day of that month."""
    y, m, d = int(s[:2]), int(s[2:4]), int(s[4:6])
    y += 2000 if y < 50 else 1900
    if d == 0:
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        return date.fromordinal(nxt.toordinal() - 1).isoformat()
    return date(y, m, d).isoformat()


def parse_gs1(data):
    """Read '(01)10036000291459(17)270315(10)LOT4471B' or the raw scan with FNC1 separators.
    Weights: AI 310n is net kg with n decimals, 320n net lb."""
    if data.startswith("]C1"):
        data = data[3:]                                  # symbology identifier some scanners add
    if "(" in data:
        pairs = re.findall(r"\((\d{2,4})\)([^(]*)", data)
        data = FNC1.join(ai + val for ai, val in pairs)
    out, i = {}, 0
    data = data.lstrip(FNC1)
    while i < len(data):
        ai2, ai4 = data[i:i + 2], data[i:i + 4]
        if ai4[:3] in ("310", "320") and ai4[3:].isdigit():
            raw = data[i + 4:i + 10]
            unit = "kg" if ai4[:3] == "310" else "lb"
            out["net_" + unit] = int(raw) / 10 ** int(ai4[3])
            i += 10
        elif ai2 in FIXED:
            val = data[i + 2:i + 2 + FIXED[ai2]]
            out[NAMES[ai2]] = _yymmdd(val) if ai2 in ("11", "13", "15", "17") else val
            i += 2 + FIXED[ai2]
        elif ai2 in VARIABLE:
            end = data.find(FNC1, i + 2)
            end = len(data) if end == -1 else end
            out[NAMES[ai2]] = data[i + 2:end][:VARIABLE[ai2]]
            i = end
        else:
            raise ValueError(f"unknown Application Identifier at: {data[i:i + 6]}")
        if i < len(data) and data[i] == FNC1:
            i += 1
    for key in ("gtin", "sscc"):
        if key in out and not is_valid(out[key]):
            raise ValueError(f"bad check digit in {key}: {out[key]}")
    return out


# ---------------------------------------------------------------- 2-prefix price/weight codes


def decode_variable(code):
    """'2 01234 0 0599 5': number system 2, item 01234, price check digit, $5.99 (or 5.99 lb, per store setting)."""
    if len(code) != 12 or code[0] != "2" or not is_valid(code):
        return None
    return {"item_code": code[1:6], "amount": int(code[7:11])}


# ---------------------------------------------------------------- the database side


def add_barcode(db, code, item_kind, item_ref, pack_level, base_qty, base_unit, source, member, at):
    """Vendor or house codes come in confirmed; a code a staff member taught HiWR waits for a manager."""
    g = to_gtin14(code)
    db.execute("INSERT INTO barcodes VALUES (?,?,?,?,?,?,?,?,?,?)",
               (g, symbology(code), item_kind, item_ref, pack_level, base_qty, base_unit, source, member, at))
    if source in ("vendor", "house"):
        db.execute("INSERT INTO barcode_confirmations VALUES (?,?,?)", (g, member, at))
    return g


def confirm_barcode(db, code, member, at):
    if level(db, member) < 2:
        raise PermissionError("a manager (level 2+) confirms a new barcode")
    g = to_gtin14(code)
    row = db.execute("SELECT added_by FROM barcodes WHERE gtin14=?", (g,)).fetchone()
    if row is None:
        raise KeyError(g)
    if row["added_by"] == member:
        raise PermissionError("the person who taught HiWR the code can't also confirm it")
    db.execute("INSERT INTO barcode_confirmations VALUES (?,?,?)", (g, member, at))


def _confirmed(db, g):
    return db.execute("SELECT 1 FROM barcode_confirmations WHERE gtin14=?", (g,)).fetchone() is not None


def lot_status(db, lot_id):
    r = db.execute("SELECT status FROM lot_events WHERE lot_id=? ORDER BY id DESC LIMIT 1", (lot_id,)).fetchone()
    return r["status"] if r else "ok"


def _lot(db, kind, ref, info, vendor, at):
    if "lot" not in info:
        return None
    db.execute("INSERT OR IGNORE INTO lots(item_kind, item_ref, lot_code, expires_on, packed_on, vendor, received_at) "
               "VALUES (?,?,?,?,?,?,?)", (kind, ref, info["lot"], info.get("expires_on") or info.get("best_before"),
                                          info.get("packed_on"), vendor, at))
    return db.execute("SELECT id FROM lots WHERE item_kind=? AND item_ref=? AND lot_code=?", (kind, ref, info["lot"])).fetchone()[0]


def scan(db, raw, member, at, purpose="receive", vendor=None, lot_tracked=()):
    """Resolve one scan. Returns a dict with result 'ok' or the reason it stopped, and logs it either way.
    lot_tracked: item refs that must carry a lot (meat, seafood, dairy, eggs, leafy greens)."""
    raw = raw.strip()
    res = {"raw": raw, "purpose": purpose}
    try:
        if not (raw.isdigit() and len(raw) in (8, 12, 13, 14)):
            info = parse_gs1(raw)
            if "gtin" not in info:
                res.update(result="pallet_label", sscc=info.get("sscc"), ask="Count the pallet or accept the vendor's list")
                return _log(db, res, member, at)
            g, res["gs1"] = info["gtin"], info
        elif (v := decode_variable(raw)) and (vm := db.execute("SELECT * FROM variable_measure_items WHERE item_code=?",
                                                                (v["item_code"],)).fetchone()):
            qty = v["amount"] / 100 if vm["embeds"] == "weight" else round(v["amount"] / vm["price_per_unit_cents"], 3)
            res.update(result="ok", gtin14=None, item_kind=vm["item_kind"], item_ref=vm["item_ref"],
                       base_qty=qty, base_unit=vm["unit"], price_cents=v["amount"] if vm["embeds"] == "price" else None)
            return _log(db, res, member, at)
        else:
            g, info = to_gtin14(raw), {}
    except ValueError as e:
        res.update(result="bad_check_digit", ask="That code didn't read cleanly. Scan again or type it.", error=str(e))
        return _log(db, res, member, at)
    g = g.zfill(14)
    res["gtin14"] = g
    b = db.execute("SELECT * FROM barcodes WHERE gtin14=?", (g,)).fetchone()
    if b is None:
        res.update(result="unknown_code", ask="What is this, and how many are inside?")
        return _log(db, res, member, at)
    res.update(item_kind=b["item_kind"], item_ref=b["item_ref"], pack_level=b["pack_level"],
               base_qty=b["base_qty"], base_unit=b["base_unit"])
    if not _confirmed(db, g):
        res.update(result="unconfirmed_code", ask="A manager needs to confirm this code once before it counts.")
        return _log(db, res, member, at)
    if "net_lb" in info or "net_kg" in info:                      # catch-weight case: the label's weight wins
        res["base_qty"] = info.get("net_lb") or round(info["net_kg"] * 2.20462, 3)
    res["lot_id"] = _lot(db, b["item_kind"], b["item_ref"], info, vendor, at)
    if res["lot_id"] and lot_status(db, res["lot_id"]) in ("on_hold", "recalled"):
        res.update(result="lot_on_hold", ask="This lot is on hold. Set it aside and tell a manager.")
    elif b["item_ref"] in lot_tracked and res["lot_id"] is None and purpose == "receive":
        res.update(result="needs_lot", ask="Type the lot and use-by date from the case, or photograph the label.")
    else:
        res["result"] = "ok"
    return _log(db, res, member, at)


def _log(db, res, member, at):
    db.execute("INSERT INTO scan_log(raw, gtin14, item_kind, item_ref, base_qty, base_unit, lot_id, purpose, result, member, at) "
               "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
               (res["raw"], res.get("gtin14"), res.get("item_kind"), res.get("item_ref"), res.get("base_qty"),
                res.get("base_unit"), res.get("lot_id"), res["purpose"], res["result"], member, at))
    return res


def set_lot_status(db, lot_id, status, reason, member, at):
    """Holds can be placed by any level-1+ receiver; release, recall and discard need a manager."""
    need = 1 if status == "on_hold" else 2
    if level(db, member) < need:
        raise PermissionError(f"level {need}+ needed to set a lot to {status}")
    db.execute("INSERT INTO lot_events(lot_id, status, reason, member, at) VALUES (?,?,?,?,?)", (lot_id, status, reason, member, at))


def trace_lot(db, lot_code):
    """Recall search: every lot with this code and every scan that touched it."""
    lots = db.execute("SELECT * FROM lots WHERE lot_code=?", (lot_code,)).fetchall()
    return [{"lot_id": l["id"], "item": l["item_ref"], "expires_on": l["expires_on"], "vendor": l["vendor"],
             "status": lot_status(db, l["id"]),
             "scans": [dict(s) for s in db.execute("SELECT purpose, base_qty, base_unit, member, at FROM scan_log "
                                                   "WHERE lot_id=? AND result='ok' ORDER BY id", (l["id"],))]}
            for l in lots]


def receive(db, res, member, at):
    """Record an 'ok' receiving scan as stock: products go to back stock, Stew ingredients to on hand.
    Both write to the append-only move logs with the scan's id in the reference."""
    if res.get("result") != "ok":
        raise ValueError(f"scan not ok: {res.get('result')}")
    sid = db.execute("SELECT MAX(id) FROM scan_log WHERE member=? AND at=?", (member, at)).fetchone()[0]
    qty = res["base_qty"]
    if res["item_kind"] == "product":
        db.execute("UPDATE products SET back_stock=back_stock+? WHERE sku=?", (int(qty), res["item_ref"]))
        db.execute("INSERT INTO stock_moves(sku, qty, from_loc, to_loc, member, at) VALUES (?,?, 'vendor', 'back', ?, ?)",
                   (res["item_ref"], int(qty), member, at))
    else:
        db.execute("UPDATE materials SET on_hand=on_hand+? WHERE material=?", (qty, res["item_ref"]))
        db.execute("INSERT INTO material_moves(material, qty, reason, ref, at) VALUES (?,?, 'receipt', ?, ?)",
                   (res["item_ref"], qty, f"scan:{sid}", at))
