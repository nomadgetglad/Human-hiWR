"""Job roles the business adds, each with an optional training checklist, and sign-offs.

A manager or the account holder adds a role (it joins the job-role pick list) and can attach training steps.
Someone in that role is a trainee until every step is signed off. A step is signed off by someone other than
the trainee who is a Manager or up, or a Lead who has finished that role's training themselves. Training never
changes anyone's access level. Append-only.
"""
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_role_defs (
    id INTEGER PRIMARY KEY, role TEXT NOT NULL UNIQUE COLLATE NOCASE, training TEXT NOT NULL,
    added_by TEXT NOT NULL, at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS training_signoffs (
    id INTEGER PRIMARY KEY, member TEXT NOT NULL, role TEXT NOT NULL, step TEXT NOT NULL,
    signed_by TEXT NOT NULL, at TEXT NOT NULL DEFAULT (datetime('now')), note TEXT,
    UNIQUE (member, role, step)
);
CREATE TABLE IF NOT EXISTS job_role_versions (     -- append-only: each save of a role's setup; the latest wins
    id INTEGER PRIMARY KEY, role TEXT NOT NULL COLLATE NOCASE, level INTEGER NOT NULL DEFAULT 0,
    reports_to TEXT, training TEXT NOT NULL, by TEXT NOT NULL, at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS job_role_versions_no_update BEFORE UPDATE ON job_role_versions BEGIN SELECT RAISE(ABORT, 'job_role_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_role_versions_no_delete BEFORE DELETE ON job_role_versions BEGIN SELECT RAISE(ABORT, 'job_role_versions is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_role_defs_no_update BEFORE UPDATE ON job_role_defs BEGIN SELECT RAISE(ABORT, 'job_role_defs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS job_role_defs_no_delete BEFORE DELETE ON job_role_defs BEGIN SELECT RAISE(ABORT, 'job_role_defs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS training_signoffs_no_update BEFORE UPDATE ON training_signoffs BEGIN SELECT RAISE(ABORT, 'training_signoffs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS training_signoffs_no_delete BEFORE DELETE ON training_signoffs BEGIN SELECT RAISE(ABORT, 'training_signoffs is append-only'); END;
"""

ADD_LEVEL = 2
MAX_STEPS = 12


def ensure(db):
    db.executescript(SCHEMA)


def clean_role(name):
    role = " ".join((name or "").split())
    if not role or len(role) > 40:
        raise ValueError("A job role needs a name, up to 40 characters")
    if not all(ch.isalpha() or ch in " -'&/" for ch in role):
        raise ValueError("Letters only in a job role (spaces, - ' & / are fine)")
    return role[0].upper() + role[1:]


def clean_steps(steps):
    out = [" ".join(s.split()) for s in (steps or []) if s and s.strip()]
    if len(out) > MAX_STEPS:
        raise ValueError(f"Up to {MAX_STEPS} training steps")
    if any(len(s) > 120 for s in out):
        raise ValueError("Keep each training step under 120 characters")
    return out


def check_add(db, level, name, steps):
    if level < ADD_LEVEL:
        raise PermissionError("Adding a job role needs Manager level")
    role = clean_role(name)
    if db.execute("SELECT 1 FROM job_role_defs WHERE role=?", (role,)).fetchone():
        raise ValueError(f"{role} is already a job role")
    return role, clean_steps(steps)


def add_role(db, level, added_by, name, steps):
    role, steps = check_add(db, level, name, steps)
    db.execute("INSERT INTO job_role_defs(role, training, added_by) VALUES (?,?,?)", (role, json.dumps(steps), added_by))
    return role


def roles(db):
    return [r["role"] for r in db.execute("SELECT role FROM job_role_defs ORDER BY role")]


def role_info(db, role):
    """The role's current setup: latest saved version, else what it was added with, else nothing set up."""
    v = db.execute("SELECT * FROM job_role_versions WHERE role=? ORDER BY id DESC LIMIT 1", (role or "",)).fetchone()
    if v:
        return {"role": v["role"], "level": v["level"], "reports_to": v["reports_to"], "steps": json.loads(v["training"]),
                "by": v["by"], "at": v["at"]}
    d = db.execute("SELECT * FROM job_role_defs WHERE role=?", (role or "",)).fetchone()
    if d:
        return {"role": d["role"], "level": 0, "reports_to": None, "steps": json.loads(d["training"]), "by": d["added_by"], "at": d["at"]}
    return {"role": role, "level": 0, "reports_to": None, "steps": [], "by": None, "at": None}


def steps_for(db, role):
    return role_info(db, role)["steps"]


def check_save(db, actor_level, role, level, reports_to, steps, known_roles):
    """A manager sets up a role: its suggested level can't be above their own (and 3 is the top a role gets);
    it reports to another known role, never itself."""
    if actor_level < ADD_LEVEL:
        raise PermissionError("Setting up a job role needs Manager level")
    role = clean_role(role)
    level = int(level or 0)
    top = min(actor_level, 3)
    if not 0 <= level <= top:
        raise PermissionError(f"You can set a role's level up to {top}; only the account holder goes higher")
    if reports_to:
        if reports_to.lower() == role.lower():
            raise ValueError("A role can't report to itself")
        if reports_to not in known_roles:
            raise ValueError("Pick who it reports to from the job roles")
    return role, level, reports_to or None, clean_steps(steps)


def save_role(db, actor, actor_level, role, level, reports_to, steps, known_roles):
    role, level, reports_to, steps = check_save(db, actor_level, role, level, reports_to, steps, known_roles)
    if not db.execute("SELECT 1 FROM job_role_defs WHERE role=?", (role,)).fetchone():
        db.execute("INSERT INTO job_role_defs(role, training, added_by) VALUES (?,?,?)", (role, json.dumps(steps), actor))
    db.execute("INSERT INTO job_role_versions(role, level, reports_to, training, by) VALUES (?,?,?,?,?)",
               (role, level, reports_to, json.dumps(steps), actor))
    return role


def progress(db, member, role):
    steps = steps_for(db, role)
    done = {r["step"]: dict(r) for r in db.execute("SELECT step, signed_by, at FROM training_signoffs WHERE member=? AND role=?",
                                                  (member, role))}
    return {"role": role, "steps": [{"step": s, **({"signed_by": done[s]["signed_by"], "at": done[s]["at"]} if s in done else {})}
                                    for s in steps],
            "done": sum(1 for s in steps if s in done), "total": len(steps),
            "trainee": bool(steps) and any(s not in done for s in steps)}


def can_sign(db, signer, signer_level, member, role):
    if signer == member:
        return False
    if signer_level >= 2:
        return True
    if signer_level >= 1:
        p = progress(db, signer, role)
        return p["total"] > 0 and not p["trainee"]
    return False


def sign_off(db, signer, signer_level, member, role, step, note=None):
    if not can_sign(db, signer, signer_level, member, role):
        raise PermissionError("Sign-offs come from a manager, or a lead who finished this training; never yourself")
    if step not in steps_for(db, role):
        raise ValueError("That step isn't part of this role's training")
    if db.execute("SELECT 1 FROM training_signoffs WHERE member=? AND role=? AND step=?", (member, role, step)).fetchone():
        raise ValueError("Already signed off")
    db.execute("INSERT INTO training_signoffs(member, role, step, signed_by, note) VALUES (?,?,?,?,?)",
               (member, role, step, signer, note))
