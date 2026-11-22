"""Signed approvals for money actions (prototype).

The plain approvals table trusts whoever writes to the database: a row that says "manager_jo approved"
is believed. This module makes each approval a digital signature that only the approver's own device can
make, and keeps every signature in a hash chain so nothing can be edited or removed afterwards without it
showing.

How it fits together
  - Every approver has a key pair. The private key stays on their phone or tablet (in real use a passkey,
    see APPROVALS_SECURITY.md); HiWR stores only the public key.
  - The account holder's public key is the root. It is pinned outside the database (printed, or in a
    config file) and passed in to verify. The account holder signs each approver's public key when
    registering it, so a key slipped into the database by someone else does not verify.
  - The requester signs the request and each approver signs the decision. What gets signed is the whole
    money action (id, scope, amount, description, requester, time requested) plus the signer and the
    decision. Change the amount after approval and every signature on it stops verifying.
  - Each signature is appended to a hash chain (each entry holds the hash of the one before). Deleting or
    editing an entry breaks the chain from that point on. Publish chain_head() somewhere else once a day
    (email to the owner, printout) and even a full rewrite of the database is caught.

The existing approve() rules still run first (certified, Finance level, not the requester, not twice,
decisions final). Signatures add proof on top; they do not replace those rules.

Needs the `cryptography` package (Ed25519). The rest of hiwr/ stays standard library only.
"""
import hashlib
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from hiwr_contacts import approve, APPROVALS_NEEDED

GENESIS = "0" * 64


# ---------- keys (in real use these live on the approver's device) ----------

def new_keypair():
    """Returns (private_key_object, public_key_hex). Sample only: real keys are made on the device."""
    k = Ed25519PrivateKey.generate()
    return k, public_hex(k)


def public_hex(private_key):
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def sign(private_key, payload: bytes) -> str:
    """What the approver's device does after showing the person the payload."""
    return private_key.sign(payload).hex()


def _verify(pub_hex, payload: bytes, sig_hex) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex)).verify(bytes.fromhex(sig_hex), payload)
        return True
    except (InvalidSignature, ValueError):
        return False


def _canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# ---------- registering approver keys (account holder only) ----------

def key_statement(member, pub_hex, status="active"):
    return _canon({"type": "approver_key", "member": member, "public_key": pub_hex, "status": status})


def register_key(db, member, pub_hex, owner_signature, owner_public, status="active"):
    """The account holder signs key_statement(member, pub_hex) on their own device and HiWR stores it.
    Revoking a lost phone is the same call with status='revoked'."""
    if not _verify(owner_public, key_statement(member, pub_hex, status), owner_signature):
        raise PermissionError("the account holder's signature on this key does not verify")
    return db.execute("INSERT INTO approver_keys(member, public_key, status, owner_signature) VALUES (?,?,?,?)",
                      (member, pub_hex, status, owner_signature)).lastrowid


def _active_key(db, member, owner_public):
    """Latest owner-signed key row for the member, if it is active. Rows that don't verify are ignored."""
    for r in db.execute("SELECT * FROM approver_keys WHERE member=? ORDER BY id DESC", (member,)):
        if _verify(owner_public, key_statement(r["member"], r["public_key"], r["status"]), r["owner_signature"]):
            return r if r["status"] == "active" else None
    return None


# ---------- what gets signed ----------

def payload(db, money_action_id, signer, role):
    """role: 'request', 'approve' or 'reject'. Built from the money action as it is stored right now."""
    a = db.execute("SELECT * FROM money_actions WHERE id=?", (money_action_id,)).fetchone()
    if a is None:
        raise LookupError(f"no money action {money_action_id}")
    return _canon({"type": "money_action", "role": role, "signer": signer, "money_action_id": a["id"],
                   "scope": a["scope"], "amount_cents": a["amount_cents"], "description": a["description"],
                   "requested_by": a["requested_by"], "requested_at": a["requested_at"], "ticket_id": a["ticket_id"]})


def _entry_hash(prev_hash, body: bytes, sig_hex):
    return hashlib.sha256(prev_hash.encode() + body + sig_hex.encode()).hexdigest()


def _append(db, money_action_id, signer, role, key_id, body, sig_hex):
    last = db.execute("SELECT hash FROM signed_decisions ORDER BY id DESC LIMIT 1").fetchone()
    prev = last["hash"] if last else GENESIS
    h = _entry_hash(prev, body, sig_hex)
    db.execute("INSERT INTO signed_decisions(money_action_id, signer, role, key_id, payload, signature, prev_hash, hash) "
               "VALUES (?,?,?,?,?,?,?,?)", (money_action_id, signer, role, key_id, body.decode(), sig_hex, prev, h))
    return h


def record_signed_request(db, money_action_id, signature, owner_public):
    """The requester signs their own request, so nobody can file one in someone else's name."""
    a = db.execute("SELECT requested_by FROM money_actions WHERE id=?", (money_action_id,)).fetchone()
    if a is None:
        raise LookupError(f"no money action {money_action_id}")
    who = a["requested_by"]
    key = _active_key(db, who, owner_public)
    if key is None:
        raise PermissionError(f"{who} has no active registered key")
    body = payload(db, money_action_id, who, "request")
    if not _verify(key["public_key"], body, signature):
        raise PermissionError(f"signature is not {who}'s, or the request changed")
    if db.execute("SELECT 1 FROM signed_decisions WHERE money_action_id=? AND role='request'", (money_action_id,)).fetchone():
        raise PermissionError("this request is already signed")
    return _append(db, money_action_id, who, "request", key["id"], body, signature)


def record_signed_decision(db, money_action_id, approver, decision, signature, owner_public):
    """Check the signature, run the normal approval rules, then add both rows. All or nothing."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision is approve or reject")
    key = _active_key(db, approver, owner_public)
    if key is None:
        raise PermissionError(f"{approver} has no active registered key")
    body = payload(db, money_action_id, approver, decision)
    if not _verify(key["public_key"], body, signature):
        raise PermissionError(f"signature is not {approver}'s, or it was made for a different action or amount")
    if not db.execute("SELECT 1 FROM signed_decisions WHERE money_action_id=? AND role='request'", (money_action_id,)).fetchone():
        raise PermissionError("the requester has not signed this request yet")
    db.execute("SAVEPOINT signed_decision")
    try:
        status = approve(db, money_action_id, approver, decision)      # certified, level, not requester, not twice, final
        _append(db, money_action_id, approver, decision, key["id"], body, signature)
    except Exception:
        db.execute("ROLLBACK TO signed_decision")
        db.execute("RELEASE signed_decision")
        raise
    db.execute("RELEASE signed_decision")
    return status


# ---------- checking ----------

def verify_chain(db):
    """Returns (ok, problems). Any edit, delete or reorder of signed_decisions shows up here."""
    prev, problems = GENESIS, []
    for r in db.execute("SELECT * FROM signed_decisions ORDER BY id"):
        if r["prev_hash"] != prev:
            problems.append(f"entry {r['id']}: chain broken (an entry before it was removed or changed)")
        if _entry_hash(r["prev_hash"], r["payload"].encode(), r["signature"]) != r["hash"]:
            problems.append(f"entry {r['id']}: contents changed after signing")
        prev = r["hash"]
    return not problems, problems


def chain_head(db):
    """Publish this outside the database once a day. A later chain that doesn't contain it was rewritten."""
    r = db.execute("SELECT hash FROM signed_decisions ORDER BY id DESC LIMIT 1").fetchone()
    return r["hash"] if r else GENESIS


def verified_status(db, money_action_id, owner_public, published_head=None):
    """Status worked out from signatures only, ignoring the plain approvals table.
    Returns (status, problems). Money should move only when status == 'APPROVED' and problems is empty."""
    problems = []
    ok, chain_problems = verify_chain(db)
    problems += chain_problems
    if published_head and not db.execute("SELECT 1 FROM signed_decisions WHERE hash=?", (published_head,)).fetchone():
        problems.append("the published chain head is missing: the log was rewritten")
    a = db.execute("SELECT * FROM money_actions WHERE id=?", (money_action_id,)).fetchone()
    if a is None:
        return "UNKNOWN", [f"no money action {money_action_id}"]
    requester_ok, approvers, rejected = False, set(), False
    for r in db.execute("SELECT * FROM signed_decisions WHERE money_action_id=? ORDER BY id", (money_action_id,)):
        key = db.execute("SELECT * FROM approver_keys WHERE id=?", (r["key_id"],)).fetchone()
        if key is None or key["member"] != r["signer"] or not _verify(
                owner_public, key_statement(key["member"], key["public_key"], key["status"]), key["owner_signature"]):
            problems.append(f"entry {r['id']}: key not signed by the account holder")
            continue
        current = payload(db, money_action_id, r["signer"], r["role"])
        if current != r["payload"].encode():
            problems.append(f"entry {r['id']}: {r['signer']} signed a different version of this action "
                            "(amount or details changed after signing)")
            continue
        if not _verify(key["public_key"], current, r["signature"]):
            problems.append(f"entry {r['id']}: signature does not verify")
            continue
        if r["role"] == "request":
            requester_ok = r["signer"] == a["requested_by"]
        elif r["signer"] == a["requested_by"]:
            problems.append(f"entry {r['id']}: requester signed their own approval; not counted")
        elif r["role"] == "reject":
            rejected = True
        else:
            approvers.add(r["signer"])
    if not requester_ok:
        problems.append("no valid signature from the requester")
    plain = {r["approver"] for r in db.execute(
        "SELECT approver FROM approvals WHERE money_action_id=? AND decision='approve'", (money_action_id,))}
    for extra in sorted(plain - approvers):
        problems.append(f"approvals table says {extra} approved, but there is no valid signature for it")
    if rejected:
        return "REJECTED", problems
    if len(approvers) >= APPROVALS_NEEDED and requester_ok:
        return "APPROVED", problems
    return f"PENDING - requires double approval ({len(approvers)}/{APPROVALS_NEEDED} signed)", problems
