"""Pharmacy prescription order status.

Stages:  received -> insurance_check -> filling -> pharmacist_verified -> ready -> picked_up
Holds:   insurance_hold, out_of_stock, prescriber_question (resume returns to where it stopped)
Rules:
  - Only someone certified for 'pharmacist_verify' (granted by the account holder) can verify.
  - Pickup only from 'ready', with ID checked.
  - Not picked up after RETURN_DAYS: flagged for return to stock and a reminder.
  - Controlled substances are not tracked in HiWR; they stay in the pharmacy system.
  - HiWR stores the pharmacy's Rx number and status only. No drug names, doses or diagnoses, in the
    record or in any message. Texts go only with SMS consent; otherwise the order goes on the call list.
This is a design starting point, not legal or HIPAA compliance advice.
"""
from datetime import datetime, timedelta
from hiwr_access import can_do
from hiwr_contacts import can_text

STAGES = ["received", "insurance_check", "filling", "pharmacist_verified", "ready", "picked_up"]
HOLDS = {"insurance_hold", "out_of_stock", "prescriber_question"}
RETURN_DAYS = 10
NOTICE = {
    "ready": "{store}: your prescription order #{ref} is ready for pickup. Bring a photo ID.",
    "insurance_hold": "{store}: your prescription order #{ref} needs an insurance update. Please call us.",
    "out_of_stock": "{store}: your prescription order #{ref} is delayed{when}. We'll let you know when it's ready.",
    "prescriber_question": "{store}: we're contacting your prescriber about order #{ref}. No action needed yet.",
    "return_warning": "{store}: order #{ref} is still waiting for pickup and will be returned to stock on {date}.",
}

def _dt(s): return datetime.fromisoformat(s)


def new_rx(db, contact_id, rx_ref, received_at, promised_hours, actor, controlled=False):
    if controlled:
        raise ValueError("controlled substances are handled only in the pharmacy system")
    rid = db.execute("INSERT INTO rx_orders(contact_id, rx_ref, received_at, promised_at) VALUES (?,?,?,?)",
                     (contact_id, rx_ref, received_at, (_dt(received_at) + timedelta(hours=promised_hours)).isoformat(timespec="minutes"))).lastrowid
    db.execute("INSERT INTO rx_events(rx_id, stage, at, actor) VALUES (?, 'received', ?, ?)", (rid, received_at, actor))
    return rid


def current(db, rx_id):
    rows = db.execute("SELECT * FROM rx_events WHERE rx_id=? ORDER BY id", (rx_id,)).fetchall()
    last = rows[-1]["stage"]
    held_at = None
    if last in HOLDS:
        held_at = next((r["stage"] for r in reversed(rows) if r["stage"] in STAGES), "received")
    return last, held_at


def _notice(db, rx_id, kind, store, **kw):
    rx = db.execute("SELECT r.*, p.e164 FROM rx_orders r LEFT JOIN contact_phones p ON p.contact_id=r.contact_id AND p.is_primary=1 "
                    "WHERE r.id=?", (rx_id,)).fetchone()
    text = NOTICE[kind].format(store=store, ref=rx["rx_ref"][-4:], when=kw.get("when", ""), date=kw.get("date", ""))
    channel = "sms" if rx["e164"] and can_text(db, rx["e164"]) else "call list"
    return {"channel": channel, "text": text}


def advance(db, rx_id, stage, at, member, note=None, store="Main St Pharmacy", id_checked=False):
    last, held_at = current(db, rx_id)
    if stage in HOLDS:
        db.execute("INSERT INTO rx_events(rx_id, stage, at, actor, note) VALUES (?,?,?,?,?)", (rx_id, stage, at, member, note))
        return _notice(db, rx_id, stage, store, when=f" (expected {note})" if note and stage == "out_of_stock" else "")
    base = held_at or last
    if stage == "resume":
        db.execute("INSERT INTO rx_events(rx_id, stage, at, actor, note) VALUES (?,?,?,?,?)", (rx_id, base, at, member, "resumed"))
        return None
    if last in HOLDS:
        raise PermissionError(f"on {last}; resume it first")
    if STAGES.index(stage) != STAGES.index(base) + 1:
        raise PermissionError(f"can't go from {base} to {stage}")
    if stage == "pharmacist_verified" and not can_do(db, member, "pharmacist_verify", at[:10]):
        raise PermissionError(f"{member} is not certified to verify prescriptions")
    if stage == "picked_up" and not id_checked:
        raise PermissionError("check the patient's photo ID before handing over")
    db.execute("INSERT INTO rx_events(rx_id, stage, at, actor, note) VALUES (?,?,?,?,?)", (rx_id, stage, at, member, note))
    return _notice(db, rx_id, "ready", store) if stage == "ready" else None


def board(db, now):
    out = []
    for r in db.execute("SELECT r.*, c.display_name FROM rx_orders r JOIN contacts c ON c.id=r.contact_id ORDER BY r.promised_at"):
        stage, held_at = current(db, r["id"])
        late = stage not in ("ready", "picked_up") and _dt(now) > _dt(r["promised_at"])
        flag = ""
        if stage == "ready":
            ready_at = db.execute("SELECT at FROM rx_events WHERE rx_id=? AND stage='ready' ORDER BY id DESC LIMIT 1", (r["id"],)).fetchone()["at"]
            if _dt(now) - _dt(ready_at) >= timedelta(days=RETURN_DAYS):
                flag = "RETURN TO STOCK"
            elif _dt(now) - _dt(ready_at) >= timedelta(days=RETURN_DAYS - 3):
                flag = f"remind: returns {(_dt(ready_at) + timedelta(days=RETURN_DAYS)).date()}"
        out.append({"rx": r["rx_ref"], "patient": r["display_name"].split()[0] + " " + r["display_name"].split()[-1][0] + ".",
                    "stage": stage + (f" (at {held_at})" if held_at else ""), "promised": r["promised_at"],
                    "late": late, "flag": flag})
    return out
