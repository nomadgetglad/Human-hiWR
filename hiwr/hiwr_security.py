"""Security patrol over sign-in data and database reads.

Human runs the patrol on a schedule (every few minutes). Code does the detecting with fixed rules; Human writes
each alert in plain words for the account holder. The patrol reads metadata only: who, when, which device (hashed),
which network (cut to /24), and what was read. It never sees passwords or PINs.
The one thing it does on its own is the standard lockout: 5 failed sign-ins in 10 minutes locks that account
for 15 minutes. Every other step (lock, reset, close the alert) is the account holder's.
"""
import hashlib
from datetime import datetime, timedelta
from hiwr_access import level
from hiwr_contacts import _is_owner

FAIL_LIMIT, FAIL_WINDOW_MIN, LOCK_MIN = 5, 10, 15
SPRAY_MEMBERS = 3
BULK_READS, BULK_WINDOW_MIN = 20, 10
PROBE_DENIED, PROBE_WINDOW_MIN = 3, 30
NEW_DEVICE_DAYS = 30
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}


def _t(s):
    return datetime.fromisoformat(s.replace(" ", "T"))


def device_hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def network_of(ip):
    return ".".join(ip.split(".")[:3]) + ".0/24"


def locked(db, member, at):
    for r in db.execute("SELECT * FROM account_locks WHERE member=? AND lifted_by IS NULL ORDER BY id DESC", (member,)):
        if r["until"] is None or _t(r["until"]) > _t(at):
            return r
    return None


def sign_in(db, member, at, ok, device_raw, ip, method="pin"):
    """Called by the app on every attempt. A locked account is refused before the PIN is even checked.
    The lockout itself happens here, at once: 5 failures in 10 minutes locks the account for 15."""
    t = _t(at)
    recent = db.execute("SELECT COUNT(*) FROM signin_events WHERE member=? AND ok=0 AND at > ? AND at <= ?",
                        (member, (t - timedelta(minutes=FAIL_WINDOW_MIN)).isoformat(sep=" "), at)).fetchone()[0]
    if recent >= FAIL_LIMIT and not locked(db, member, at):
        db.execute("INSERT INTO account_locks(member, until, reason, by, at) VALUES (?,?,?,?,?)",
                   (member, (t + timedelta(minutes=LOCK_MIN)).isoformat(sep=" "), "too many failed sign-ins", "patrol", at))
    if locked(db, member, at):
        ok = False
    db.execute("INSERT INTO signin_events(member, at, ok, device_hash, network, method) VALUES (?,?,?,?,?,?)",
               (member, at, int(ok), device_hash(device_raw), network_of(ip), method))
    return bool(ok)


def _alert(db, at, kind, member, severity, detail):
    db.execute("INSERT OR IGNORE INTO security_alerts(at, kind, member, severity, detail) VALUES (?,?,?,?,?)",
               (at, kind, member, severity, detail))


def patrol(db, now, since_min=60):
    """Look over the last `since_min` minutes. Returns the open alerts, worst first."""
    t0 = (_t(now) - timedelta(minutes=since_min)).isoformat(sep=" ")
    ev = db.execute("SELECT * FROM signin_events WHERE at >= ? ORDER BY at", (t0,)).fetchall()

    # 1. guessing a PIN: many failures on one account
    for m in {e["member"] for e in ev}:
        fails = [e for e in ev if e["member"] == m and not e["ok"]]
        for i in range(len(fails) - FAIL_LIMIT + 1):
            a, b = _t(fails[i]["at"]), _t(fails[i + FAIL_LIMIT - 1]["at"])
            if b - a <= timedelta(minutes=FAIL_WINDOW_MIN):
                at = fails[i + FAIL_LIMIT - 1]["at"]
                if not db.execute("SELECT 1 FROM account_locks WHERE member=? AND at >= ?", (m, fails[i]["at"])).fetchone():
                    db.execute("INSERT INTO account_locks(member, until, reason, by, at) VALUES (?,?,?,?,?)",
                               (m, (b + timedelta(minutes=LOCK_MIN)).isoformat(sep=" "), "too many failed sign-ins", "patrol", at))
                nets = sorted({f["network"] for f in fails})
                _alert(db, at, "failed_signins", m, "high",
                       f"{len(fails)} failed sign-ins on {m}'s account within {FAIL_WINDOW_MIN} min from {', '.join(nets)}. "
                       f"Locked for {LOCK_MIN} min automatically.")
                break

    # 2. one network trying many accounts
    for net in {e["network"] for e in ev}:
        tried = sorted({e["member"] for e in ev if e["network"] == net and not e["ok"]})
        if len(tried) >= SPRAY_MEMBERS:
            last = max(e["at"] for e in ev if e["network"] == net)
            _alert(db, last, "many_accounts_one_network", None, "high",
                   f"Network {net} failed sign-ins on {len(tried)} accounts ({', '.join(tried)}). Someone may be trying common PINs.")

    for e in ev:
        if not e["ok"]:
            continue
        m, lvl = e["member"], level(db, e["member"])
        # 3. new device
        seen = db.execute("SELECT 1 FROM signin_events WHERE member=? AND device_hash=? AND ok=1 AND at < ? AND at >= ?",
                          (m, e["device_hash"], e["at"], (_t(e["at"]) - timedelta(days=NEW_DEVICE_DAYS)).isoformat(sep=" "))).fetchone()
        history = db.execute("SELECT 1 FROM signin_events WHERE member=? AND ok=1 AND at < ?", (m, e["at"])).fetchone()
        if history and not seen:
            _alert(db, e["at"], "new_device", m, "high" if lvl >= 3 else "medium",
                   f"{m} (level {lvl}) signed in from a device not used in {NEW_DEVICE_DAYS} days, network {e['network']}. "
                   "Ask them if it was them" + ("; this account can see payroll and money approvals" if lvl >= 3 else ""))
        # 4. outside usual hours
        p = db.execute("SELECT * FROM security_profiles WHERE member=?", (m,)).fetchone()
        h = _t(e["at"]).hour
        if p and not (p["usual_start"] <= h < p["usual_end"]):
            _alert(db, e["at"], "off_hours", m, "medium" if lvl >= 2 else "low",
                   f"{m} signed in at {_t(e['at']):%H:%M}; usual hours are {p['usual_start']}:00 to {p['usual_end']}:00")

    # 5. reading many client records fast, or exporting them
    reads = db.execute("SELECT * FROM access_log WHERE at >= ? AND (action LIKE 'view%' OR action LIKE 'export%') "
                       "AND contact_id IS NOT NULL ORDER BY at", (t0,)).fetchall()
    for actor in {r["actor"] for r in reads}:
        mine = [r for r in reads if r["actor"] == actor]
        for i in range(len(mine) - BULK_READS + 1):
            if _t(mine[i + BULK_READS - 1]["at"]) - _t(mine[i]["at"]) <= timedelta(minutes=BULK_WINDOW_MIN):
                n = len({r["contact_id"] for r in mine})
                _alert(db, mine[i + BULK_READS - 1]["at"], "bulk_client_reads", actor, "high",
                       f"{actor} opened {n} different client records within {BULK_WINDOW_MIN} min. Normal work opens a few at a time")
                break
        for r in mine:
            if r["action"].startswith("export") and level(db, actor) < 3:
                _alert(db, r["at"], "client_export", actor, "high", f"{actor} (level {level(db, actor)}) exported client record #{r['contact_id']}")

    # 6. probing: repeatedly asking for data the level doesn't allow
    denied = db.execute("SELECT actor, COUNT(*) n, MAX(at) last, GROUP_CONCAT(DISTINCT substr(action, 8)) what FROM access_log "
                        "WHERE at >= ? AND action LIKE 'denied:%' GROUP BY actor", (t0,)).fetchall()
    for d in denied:
        if d["n"] >= PROBE_DENIED:
            _alert(db, d["last"], "repeated_denied", d["actor"], "medium",
                   f"{d['actor']} was refused {d['n']} times ({d['what']}). Could be a wrong button or someone looking where they shouldn't")

    # 7. access changes: always reported to the owner
    for r in db.execute("SELECT * FROM member_levels WHERE at >= ?", (t0,)):
        _alert(db, r["at"], "level_change", r["member"], "info", f"{r['set_by']} set {r['member']} to level {r['level']}")
    for r in db.execute("SELECT * FROM task_grants WHERE at >= ?", (t0,)):
        _alert(db, r["at"], "task_grant", r["member"], "info", f"{r['granted_by']} certified {r['member']} for {r['task']}")

    return open_alerts(db)


def open_alerts(db):
    rows = db.execute("SELECT * FROM security_alerts WHERE status='open'").fetchall()
    return sorted(rows, key=lambda r: (SEVERITY_ORDER[r["severity"]], r["at"]))


def decide(db, owner, alert_id, decision, at, note=None):
    """decision: 'was_them' (close), 'lock' (until lifted), 'reset' (lock and require a new PIN), 'unlock'."""
    if not _is_owner(db, owner):
        raise PermissionError("security alerts are decided by the account holder")
    a = db.execute("SELECT * FROM security_alerts WHERE id=?", (alert_id,)).fetchone()
    if decision in ("lock", "reset") and a["member"]:
        db.execute("INSERT INTO account_locks(member, until, reason, by, at) VALUES (?,?,?,?,?)",
                   (a["member"], None, f"{decision}: {a['kind']}" + (f" ({note})" if note else ""), owner, at))
    if decision == "unlock" and a["member"]:
        db.execute("UPDATE account_locks SET lifted_by=? WHERE member=? AND lifted_by IS NULL", (owner, a["member"]))
    db.execute("UPDATE security_alerts SET status='closed', decided_by=?, decision=? WHERE id=?",
               (owner, decision + (f": {note}" if note else ""), alert_id))


def brief(db, now):
    """Plain-words summary for the owner's Out circle."""
    al = open_alerts(db)
    if not al:
        return f"Security patrol {now[11:16]}: nothing unusual."
    counts = {s: sum(a["severity"] == s for a in al) for s in SEVERITY_ORDER}
    head = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
    return f"Security patrol {now[11:16]}: {head}.\n" + "\n".join(
        f"   #{a['id']} [{a['severity']}] {a['at'][11:16]} {a['detail']}" for a in al)
