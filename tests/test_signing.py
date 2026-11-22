"""Signed approvals: forging, replaying, editing or deleting an approval is caught."""
import pytest

pytest.importorskip("cryptography")

import hiwr_signing as S  # noqa: E402
from hiwr_contacts import request_money_action, money_action_status, certify  # noqa: E402
from hiwr_access import set_level  # noqa: E402
from conftest import OWNER  # noqa: E402

PEOPLE = ("chef_marco", "manager_jo", "accounting_sue", "finance_kim")


@pytest.fixture
def world(db):
    owner_key, owner_pub = S.new_keypair()           # owner_pub is pinned outside the database
    keys = {}
    for m in PEOPLE:
        k, pub = S.new_keypair()
        S.register_key(db, m, pub, S.sign(owner_key, S.key_statement(m, pub)), owner_pub)
        keys[m] = k
    return db, owner_key, owner_pub, keys


def new_action(world, cents=3000, by="chef_marco"):
    db, _, owner_pub, keys = world
    a = request_money_action(db, "refund", cents, "Refund 2 plates fish tacos, table 12", by)
    S.record_signed_request(db, a, S.sign(keys[by], S.payload(db, a, by, "request")), owner_pub)
    return a


def sign_decision(world, a, who, decision="approve", key_of=None):
    db, _, owner_pub, keys = world
    sig = S.sign(keys[key_of or who], S.payload(db, a, who, decision))
    return S.record_signed_decision(db, a, who, decision, sig, owner_pub)


def test_happy_path(world):
    db, _, owner_pub, _ = world
    a = new_action(world)
    assert sign_decision(world, a, "manager_jo").startswith("PENDING")
    assert sign_decision(world, a, "accounting_sue") == "APPROVED"
    assert S.verified_status(db, a, owner_pub) == ("APPROVED", [])
    assert S.verify_chain(db) == (True, [])


def test_forged_signature_with_someone_elses_key(world):
    a = new_action(world)
    with pytest.raises(PermissionError, match="signature"):
        sign_decision(world, a, "accounting_sue", key_of="manager_jo")     # jo signs as sue
    assert money_action_status(world[0], a).startswith("PENDING")


def test_one_person_cannot_be_both_approvers(world):
    a = new_action(world)
    sign_decision(world, a, "manager_jo")
    with pytest.raises(PermissionError):
        sign_decision(world, a, "manager_jo")


def test_requester_cannot_sign_approval(world):
    db = world[0]
    set_level(db, "chef_marco", 3, OWNER)
    certify(db, "chef_marco", "refund", OWNER)
    a = new_action(world)
    with pytest.raises(PermissionError, match="requested"):
        sign_decision(world, a, "chef_marco")


def test_request_in_someone_elses_name_needs_their_signature(world):
    """Closes the plain-table gap: jo files a refund as sue, but can't make sue's request signature."""
    db, _, owner_pub, keys = world
    a = request_money_action(db, "refund", 9999, "x", "accounting_sue")
    with pytest.raises(PermissionError):
        S.record_signed_request(db, a, S.sign(keys["manager_jo"], S.payload(db, a, "accounting_sue", "request")), owner_pub)
    with pytest.raises(PermissionError, match="not signed"):
        sign_decision(world, a, "manager_jo")


def test_replay_signature_on_another_action(world):
    db, _, owner_pub, keys = world
    a = new_action(world, 3000)
    b = new_action(world, 3000)
    sig_for_a = S.sign(keys["manager_jo"], S.payload(db, a, "manager_jo", "approve"))
    S.record_signed_decision(db, a, "manager_jo", "approve", sig_for_a, owner_pub)
    with pytest.raises(PermissionError, match="different action"):
        S.record_signed_decision(db, b, "manager_jo", "approve", sig_for_a, owner_pub)


def test_approve_signature_cannot_be_used_as_reject(world):
    db, _, owner_pub, keys = world
    a = new_action(world)
    sig = S.sign(keys["manager_jo"], S.payload(db, a, "manager_jo", "approve"))
    with pytest.raises(PermissionError):
        S.record_signed_decision(db, a, "manager_jo", "reject", sig, owner_pub)


def test_amount_edited_behind_the_triggers_is_caught(world):
    """Someone with the database file drops the trigger and changes $30 to $5,000."""
    db, _, owner_pub, _ = world
    a = new_action(world)
    sign_decision(world, a, "manager_jo"); sign_decision(world, a, "accounting_sue")
    db.execute("DROP TRIGGER money_actions_no_update")
    db.execute("UPDATE money_actions SET amount_cents=500000 WHERE id=?", (a,))
    assert money_action_status(db, a) == "APPROVED"            # plain table is fooled
    status, problems = S.verified_status(db, a, owner_pub)
    assert status != "APPROVED"
    assert any("different version" in p for p in problems)


def test_fake_approval_row_inserted_directly_is_caught(world):
    db, _, owner_pub, _ = world
    a = new_action(world)
    sign_decision(world, a, "manager_jo")
    db.execute("INSERT INTO approvals(money_action_id, approver, decision) VALUES (?,?,?)", (a, "accounting_sue", "approve"))
    assert money_action_status(db, a) == "APPROVED"            # plain table is fooled
    status, problems = S.verified_status(db, a, owner_pub)
    assert status.startswith("PENDING")
    assert any("no valid signature" in p for p in problems)


def test_key_planted_without_owner_signature(world):
    db, _, owner_pub, _ = world
    k, pub = S.new_keypair()
    with pytest.raises(PermissionError):
        S.register_key(db, "mallory", pub, S.sign(k, S.key_statement("mallory", pub)), owner_pub)
    # planted straight into the table: ignored
    db.execute("INSERT INTO approver_keys(member, public_key, status, owner_signature) VALUES (?,?,?,?)",
               ("accounting_sue", pub, "active", "00" * 64))
    a = new_action(world)
    sig = S.sign(k, S.payload(db, a, "accounting_sue", "approve"))
    with pytest.raises(PermissionError):
        S.record_signed_decision(db, a, "accounting_sue", "approve", sig, owner_pub)


def test_revoked_key_cannot_sign(world):
    db, owner_key, owner_pub, keys = world
    pub = S.public_hex(keys["manager_jo"])
    S.register_key(db, "manager_jo", pub, S.sign(owner_key, S.key_statement("manager_jo", pub, "revoked")), owner_pub, "revoked")
    a = new_action(world)
    with pytest.raises(PermissionError, match="no active"):
        sign_decision(world, a, "manager_jo")


def test_deleted_chain_entry_is_caught(world):
    db, _, owner_pub, _ = world
    a = new_action(world)
    sign_decision(world, a, "manager_jo"); sign_decision(world, a, "accounting_sue")
    db.execute("DROP TRIGGER signed_decisions_no_delete")
    db.execute("DELETE FROM signed_decisions WHERE signer='manager_jo'")
    ok, problems = S.verify_chain(db)
    assert not ok and "chain broken" in problems[0]


def test_edited_chain_entry_is_caught(world):
    db, _, owner_pub, _ = world
    a = new_action(world)
    sign_decision(world, a, "manager_jo")
    db.execute("DROP TRIGGER signed_decisions_no_update")
    db.execute("UPDATE signed_decisions SET payload=replace(payload, '3000', '9000') WHERE signer='manager_jo'")
    assert not S.verify_chain(db)[0]


def test_full_rewrite_caught_by_published_head(world):
    """An attacker rebuilds the whole chain. The head published yesterday is no longer in it."""
    db, _, owner_pub, _ = world
    a = new_action(world)
    sign_decision(world, a, "manager_jo")
    yesterday = S.chain_head(db)
    db.execute("DROP TRIGGER signed_decisions_no_delete")
    db.execute("DELETE FROM signed_decisions")
    status, problems = S.verified_status(db, a, owner_pub, published_head=yesterday)
    assert any("rewritten" in p for p in problems)


def test_failed_rule_leaves_no_signature_behind(world):
    """A valid signature from someone not certified is refused and nothing is written."""
    db, owner_key, owner_pub, keys = world
    k, pub = S.new_keypair()
    S.register_key(db, "cook_luis", pub, S.sign(owner_key, S.key_statement("cook_luis", pub)), owner_pub)
    keys["cook_luis"] = k
    a = new_action(world)
    before = db.execute("SELECT COUNT(*) FROM signed_decisions").fetchone()[0]
    with pytest.raises(PermissionError, match="not certified"):
        sign_decision(world, a, "cook_luis")
    assert db.execute("SELECT COUNT(*) FROM signed_decisions").fetchone()[0] == before
