"""Authenticator sign-in and employee ID intake: rules in hiwr_auth / hiwr_idintake, and attacks through the API."""
import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import hiwr_auth as auth
import hiwr_idintake as idin
from hiwr_vault import Vault
from server import app as appmod
from server import seed

T0 = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
ID = {"legal_name": "Rosa Maria Delgado", "birth_date": "1994-03-02", "doc_type": "drivers_license",
      "doc_number": "D123-4567-8901", "issuer": "NC", "expires_on": "2030-03-02", "address": "12 Market St, Wilmington",
      "emergency_name": "Luis", "emergency_phone": "+1 910 555 0101", "work_auth_verified": True}


@pytest.fixture
def d():
    appmod.reset()            # sample restaurant: owner_alex 4, accounting_sue 3, manager_jo 3, chef_marco 2, fry_rosa 0
    return appmod.db()


@pytest.fixture
def v():
    return appmod.STATE["vault"]


def enroll(d, v, member, issuer="owner_alex", now=T0):
    code = auth.issue_setup_code(d, issuer, member, now)
    secret = auth.new_secret()
    auth.enroll(d, v, member, code, secret, auth.totp(secret, auth._step(now)), now)
    return secret


# ---------- TOTP ----------

def test_totp_matches_rfc_6238():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert auth.totp(secret, 59 // 30) == "287082"            # RFC 6238 test vector (SHA1, 8 digits -> last 6)
    assert auth.totp(secret, 1111111109 // 30) == "081804"


def test_codes_one_step_either_side_only():
    s = auth.new_secret()
    st = auth._step(T0)
    assert auth.match_step(s, auth.totp(s, st - 1), T0) == st - 1
    assert auth.match_step(s, auth.totp(s, st + 2), T0) is None
    assert auth.match_step(s, "12345", T0) is None


# ---------- setup codes ----------

def test_who_issues_setup_codes(d):
    assert auth.can_issue(d, "chef_marco", "fry_rosa")           # manager -> staff
    assert not auth.can_issue(d, "fry_rosa", "stocker_tim")      # staff can't
    assert not auth.can_issue(d, "chef_marco", "manager_jo")     # not for someone at or above your level
    assert not auth.can_issue(d, "manager_jo", "owner_alex")     # owner's code comes from the console
    assert auth.can_issue(d, "owner_alex", "manager_jo")
    assert not auth.can_issue(d, "owner_alex", "owner_alex")     # not even the owner, from inside the app
    with pytest.raises(PermissionError, match="console"):
        auth.issue_setup_code(d, "manager_jo", "owner_alex")


def test_setup_code_is_one_use_and_expires(d, v):
    code = auth.issue_setup_code(d, "chef_marco", "fry_rosa", T0)
    s = auth.new_secret()
    auth.enroll(d, v, "fry_rosa", code, s, auth.totp(s, auth._step(T0)), T0)
    with pytest.raises(PermissionError):
        auth.check_setup_code(d, "fry_rosa", code, T0)
    old = auth.issue_setup_code(d, "chef_marco", "stocker_tim", T0)
    with pytest.raises(PermissionError):
        auth.check_setup_code(d, "stocker_tim", old, T0 + timedelta(hours=25))


def test_only_newest_setup_code_works(d):
    a = auth.issue_setup_code(d, "chef_marco", "fry_rosa", T0)
    b = auth.issue_setup_code(d, "chef_marco", "fry_rosa", T0)
    with pytest.raises(PermissionError):
        auth.check_setup_code(d, "fry_rosa", a, T0)
    assert auth.check_setup_code(d, "fry_rosa", b.lower(), T0)            # case and dashes don't matter


def test_codes_stored_only_as_hashes(d, v):
    code = auth.issue_setup_code(d, "chef_marco", "fry_rosa", T0)
    s = auth.new_secret()
    auth.enroll(d, v, "fry_rosa", code, s, auth.totp(s, auth._step(T0)), T0)
    dump = "\n".join(d.iterdump())
    assert code not in dump and s not in dump


# ---------- sign-in ----------

def test_sign_in_and_replay_blocked(d, v):
    s = enroll(d, v, "fry_rosa")
    later = T0 + timedelta(minutes=5)
    tok, _ = auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(later)), later)
    assert auth.session_member(d, tok, later) == "fry_rosa"
    with pytest.raises(PermissionError):
        auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(later)), later)       # same code again
    with pytest.raises(PermissionError):
        auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(T0)), later)          # the enrollment code


def test_five_wrong_codes_lock_for_15_minutes(d, v):
    s = enroll(d, v, "fry_rosa")
    t = T0 + timedelta(minutes=1)
    for _ in range(5):
        with pytest.raises(PermissionError):
            auth.sign_in(d, v, "fry_rosa", "000000", t)
    with pytest.raises(PermissionError, match="Too many"):
        auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(t)), t)
    t2 = t + timedelta(minutes=16)
    assert auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(t2)), t2)
    assert "locked" in [e["kind"] for e in auth.history(d, "fry_rosa")]


def test_session_expires_after_a_shift_and_on_sign_out(d, v):
    s = enroll(d, v, "fry_rosa")
    t = T0 + timedelta(minutes=1)
    tok, _ = auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(t)), t)
    assert auth.session_member(d, tok, t + timedelta(hours=13)) is None
    assert auth.sign_out(d, tok, t) == "fry_rosa"
    assert auth.session_member(d, tok, t) is None


def test_reset_replaces_authenticator_and_ends_sessions(d, v):
    s = enroll(d, v, "fry_rosa")
    t = T0 + timedelta(minutes=1)
    tok, _ = auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(t)), t)
    s2 = enroll(d, v, "fry_rosa", issuer="chef_marco", now=t + timedelta(minutes=2))
    assert auth.session_member(d, tok, t) is None
    t3 = t + timedelta(minutes=5)
    with pytest.raises(PermissionError):
        auth.sign_in(d, v, "fry_rosa", auth.totp(s, auth._step(t3)), t3)             # the lost phone
    assert auth.sign_in(d, v, "fry_rosa", auth.totp(s2, auth._step(t3)), t3)
    assert "reset" in [e["kind"] for e in auth.history(d, "fry_rosa")]


def test_history_has_method_device_and_time(d, v):
    code = auth.issue_setup_code(d, "chef_marco", "fry_rosa", T0)
    s = auth.new_secret()
    auth.enroll(d, v, "fry_rosa", code, s, auth.totp(s, auth._step(T0)), T0, device="Chrome on Android", ip="10.0.0.5")
    ev = auth.history(d, "fry_rosa")
    assert [e["kind"] for e in ev] == ["enrolled", "setup_code_issued"]
    assert ev[0]["device"] == "Chrome on Android" and ev[0]["method"] == "totp" and ev[0]["actor"] == "chef_marco"
    block = auth.settings_block(d, ["fry_rosa", "chef_marco"])
    assert block["status"] == "partial" and block["events"][0]["event"] == "enrolled"
    assert "Chrome on Android" in block["events"][0]["origin"]


def test_device_labels():
    assert auth.device_label("Mozilla/5.0 (Linux; Android 14) AppleWebKit Chrome/120 Mobile Safari/537") == "Chrome on Android"
    assert auth.device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17) AppleWebKit Version/17 Mobile Safari/604") == "Safari on iPhone"


def test_secret_needs_the_right_key(d, v):
    s = enroll(d, v, "fry_rosa")
    with pytest.raises(PermissionError, match="human.key"):
        auth.sign_in(d, Vault.ephemeral(), "fry_rosa", auth.totp(s, auth._step(T0 + timedelta(minutes=1))), T0 + timedelta(minutes=1))


# ---------- ID intake ----------

def test_manager_enters_id_but_cannot_open_it(d, v):
    idin.record_intake(d, v, "chef_marco", "fry_rosa", ID)
    st = idin.id_status(d, "fry_rosa")
    assert st["on_file"] and st["doc_type"] == "drivers_license" and st["work_auth_verified"]
    assert "legal_name" not in st and "doc_number" not in st
    with pytest.raises(PermissionError):
        idin.view_details(d, v, "chef_marco", "fry_rosa")


def test_finance_and_owner_open_details_and_it_is_logged(d, v):
    idin.record_intake(d, v, "chef_marco", "fry_rosa", ID)
    out = idin.view_details(d, v, "accounting_sue", "fry_rosa")
    assert out["legal_name"] == "Rosa Maria Delgado" and out["doc_number"] == "D123-4567-8901"
    idin.view_details(d, v, "owner_alex", "fry_rosa")
    log = idin.access_log(d, "fry_rosa")
    assert [(x["viewer"], x["what"]) for x in log] == [("owner_alex", "details"), ("accounting_sue", "details"), ("chef_marco", "entered")]


def test_who_can_enter_id(d, v):
    with pytest.raises(PermissionError):
        idin.check_intake(d, "fry_rosa", "stocker_tim", ID)          # staff
    with pytest.raises(PermissionError):
        idin.check_intake(d, "chef_marco", "chef_marco", ID)         # own ID
    with pytest.raises(PermissionError):
        idin.check_intake(d, "chef_marco", "manager_jo", ID)         # above your level
    assert idin.check_intake(d, "owner_alex", "manager_jo", ID)


def test_task_grants_never_unlock_id_details(d, v):
    from hiwr_access import grant_task
    idin.record_intake(d, v, "chef_marco", "fry_rosa", ID)
    grant_task(d, "chef_marco", "hr_manager", "owner_alex", seed.TODAY)
    with pytest.raises(PermissionError):
        idin.view_details(d, v, "chef_marco", "fry_rosa")


def test_details_are_encrypted_at_rest(d, v):
    idin.record_intake(d, v, "chef_marco", "fry_rosa", ID, base64.b64encode(PNG).decode())
    dump = "\n".join(d.iterdump())
    for secret in ("Rosa Maria Delgado", "D1234567", "D123-4567-8901", "1994-03-02", "Market St", "555 0101"):
        assert secret not in dump


@pytest.mark.parametrize("field,value,msg", [
    ("doc_type", "library_card", "kind of ID"),
    ("birth_date", "2020-01-01", "14 or older"),
    ("birth_date", "31/05/1990", "date like"),
    ("expires_on", "2020-01-01", "expired"),
    ("expires_on", "", "Expiry date is required"),
    ("doc_number", "12", "Document number"),
    ("legal_name", "", "Legal name"),
    ("emergency_phone", "call me", "phone"),
])
def test_bad_id_input_refused(d, field, value, msg):
    with pytest.raises(ValueError, match=msg):
        idin.check_intake(d, "chef_marco", "fry_rosa", {**ID, field: value})


def test_photo_must_be_jpeg_or_png_under_5mb(d):
    with pytest.raises(ValueError, match="JPEG or PNG"):
        idin.check_intake(d, "chef_marco", "fry_rosa", ID, base64.b64encode(b"%PDF-1.4 hello").decode())
    with pytest.raises(ValueError, match="5 MB"):
        idin.check_intake(d, "chef_marco", "fry_rosa", ID, base64.b64encode(PNG + b"\0" * (5 * 1024 * 1024)).decode())
    assert idin.check_intake(d, "chef_marco", "fry_rosa", ID, "data:image/png;base64," + base64.b64encode(PNG).decode())[1] == PNG


def test_corrections_add_versions(d, v):
    idin.record_intake(d, v, "chef_marco", "fry_rosa", ID)
    idin.record_intake(d, v, "chef_marco", "fry_rosa", {**ID, "issuer": "SC"})
    assert idin.id_status(d, "fry_rosa")["versions"] == 2
    assert idin.view_details(d, v, "owner_alex", "fry_rosa")["issuer"] == "SC"
    with pytest.raises(Exception, match="append-only"):
        d.execute("UPDATE id_records SET doc_type='other'")


def test_expiring_ids_listed(d, v):
    from datetime import date
    idin.record_intake(d, v, "chef_marco", "fry_rosa", {**ID, "expires_on": "2026-12-10"}, now=T0)
    assert idin.expiring(d, date(2026, 11, 26)) == [{"member": "fry_rosa", "expires_on": "2026-12-10", "days": 14}]


# ---------- through the API ----------

def H(m):
    return {"X-Member": m}


def card(c, m, kind, params, headers=None):
    r = c.post("/api/proposals", json={"kind": kind, "params": params}, headers={**H(m), **(headers or {})})
    if r.status_code != 200:
        return r, r
    return r, c.post(f"/api/proposals/{r.json()['id']}/confirm", json={}, headers={**H(m), **(headers or {})})


@pytest.fixture
def c():
    appmod.reset()
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_id_card_does_not_echo_details(c):
    r, done = card(c, "chef_marco", "id_intake", {"member_id": "fry_rosa", "fields": ID})
    body = json.dumps(r.json()) + json.dumps(done.json())
    assert done.status_code == 200
    assert "Rosa Maria" not in body and "D123-4567-8901" not in body and "1994" not in body
    assert "8901" in body                                      # last 4 only, to check against the card
    stored = json.dumps(appmod.PROPOSALS, default=str)
    assert "Rosa Maria" not in stored                          # dropped from memory once confirmed


def test_id_endpoints_by_level(c):
    card(c, "chef_marco", "id_intake", {"member_id": "fry_rosa", "fields": ID, "photo": base64.b64encode(PNG).decode()})
    assert c.get("/api/team/fry_rosa/id", headers=H("chef_marco")).status_code == 403
    assert c.get("/api/team/fry_rosa/id/photo", headers=H("chef_marco")).status_code == 403
    r = c.get("/api/team/fry_rosa/id", headers=H("accounting_sue"))
    assert r.status_code == 200 and r.json()["legal_name"] == "Rosa Maria Delgado"
    p = c.get("/api/team/fry_rosa/id/photo", headers=H("owner_alex"))
    assert p.status_code == 200 and p.content == PNG and p.headers["cache-control"] == "no-store"
    team = c.get("/api/team", headers=H("chef_marco")).json()
    rosa = next(m for m in team["members"] if m["id"] == "fry_rosa")
    assert rosa["id_card"]["on_file"] and "legal_name" not in json.dumps(team) and team["can_view_id"] is False


def test_setup_code_shows_once(c):
    _, done = card(c, "chef_marco", "setup_code", {"member_id": "fry_rosa"})
    assert done.status_code == 200 and "Setup code for fry_rosa:" in done.json()["result"]
    code = done.json()["result"].split(": ")[1][:9]
    assert code not in json.dumps(appmod.PROPOSALS, default=str)
    assert card(c, "chef_marco", "setup_code", {"member_id": "manager_jo"})[0].status_code == 403
    assert card(c, "fry_rosa", "setup_code", {"member_id": "stocker_tim"})[0].status_code == 403


def test_phone_setup_then_sign_in(c):
    from test_roster import sign_in_first_time
    _, done = card(c, "chef_marco", "setup_code", {"member_id": "fry_rosa"})
    phone = TestClient(appmod.app, raise_server_exceptions=False)
    sign_in_first_time(phone, "fry_rosa", done.json()["result"].split(": ")[1][:9])
    assert phone.get("/api/me").json()["id"] == "fry_rosa"
    assert phone.get("/api/auth/status").json()["signed_in"] == "fry_rosa"
    h = c.get("/api/auth/history?member_id=fry_rosa", headers=H("chef_marco")).json()
    assert h["enrolled"] and h["events"][0]["kind"] == "enrolled" and h["events"][0]["device"] == "test client on unknown device"
    assert c.get("/api/auth/history?member_id=chef_marco", headers=H("fry_rosa")).status_code == 403
    phone.post("/api/auth/signout")
    assert phone.get("/api/me").status_code == 401


def test_wrong_setup_code_and_unknown_member(c):
    assert c.post("/api/auth/setup/start", json={"member_id": "fry_rosa", "setup_code": "AAAA-BBBB"}).status_code == 403
    assert c.post("/api/auth/signin", json={"member_id": "nobody", "code": "123456"}).status_code == 401
    assert c.post("/api/auth/signin", json={"member_id": "fry_rosa", "code": "123456"}).status_code == 403


@pytest.fixture
def own():
    appmod.reset(sample=False)
    yield TestClient(appmod.app, raise_server_exceptions=False)
    appmod.reset()


def test_own_account_ignores_the_switcher_and_needs_x_human(own):
    from test_roster import sign_in_first_time
    assert own.get("/api/me", headers=H("owner_alex")).status_code == 401
    # a POST from another site (no X-Human header) is refused even with a good cookie
    assert own.post("/api/auth/setup/start", json={"member_id": "owner_alex", "setup_code": appmod.STATE["owner_code"]}).status_code == 403
    own.headers["X-Human"] = "1"
    sign_in_first_time(own, "owner_alex", appmod.STATE["owner_code"])
    assert own.get("/api/me").json()["level"] == 4
    del own.headers["X-Human"]
    r = own.post("/api/proposals", json={"kind": "add_member", "params": {"first_name": "Maria", "title": "Cook"}})
    assert r.status_code == 403 and "X-Human" in r.text


def test_session_cookie_is_httponly_and_strict(own):
    own.headers["X-Human"] = "1"
    from hiwr_auth import totp, _step
    r = own.post("/api/auth/setup/start", json={"member_id": "owner_alex", "setup_code": appmod.STATE["owner_code"]})
    assert r.json()["qr_svg"].startswith("<svg") and r.json()["otpauth"].startswith("otpauth://totp/")
    r = own.post("/api/auth/setup/finish", json={"member_id": "owner_alex", "setup_code": appmod.STATE["owner_code"],
                                                 "code": totp(r.json()["secret"], _step(datetime.now(timezone.utc)))})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc
