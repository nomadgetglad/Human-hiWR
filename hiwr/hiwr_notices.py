"""Notices that turn into scheduled tasks, e.g. a deep-clean notice.

A manager posts a notice for a week. Each area on the notice becomes a dated task for someone who closes that
night on the schedule, spread across the week so no one gets two in a row. Tasks keep an append-only history
(open -> done), like everything else. Standard library only.
"""
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    posted_by   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    title       TEXT NOT NULL,
    week_start  TEXT NOT NULL,
    areas       TEXT NOT NULL              -- JSON list
);
CREATE TABLE IF NOT EXISTS notice_tasks (
    id          INTEGER PRIMARY KEY,
    notice_id   INTEGER NOT NULL REFERENCES notices(id),
    member      TEXT NOT NULL,
    area        TEXT NOT NULL,
    due_on      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notice_task_events (   -- append-only
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES notice_tasks(id),
    status      TEXT NOT NULL,                  -- open, done
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    actor       TEXT NOT NULL,
    note        TEXT
);
CREATE TRIGGER IF NOT EXISTS notices_no_update BEFORE UPDATE ON notices BEGIN SELECT RAISE(ABORT, 'notices is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notices_no_delete BEFORE DELETE ON notices BEGIN SELECT RAISE(ABORT, 'notices is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notice_tasks_no_update BEFORE UPDATE ON notice_tasks BEGIN SELECT RAISE(ABORT, 'notice_tasks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notice_tasks_no_delete BEFORE DELETE ON notice_tasks BEGIN SELECT RAISE(ABORT, 'notice_tasks is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notice_task_events_no_update BEFORE UPDATE ON notice_task_events BEGIN SELECT RAISE(ABORT, 'notice_task_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS notice_task_events_no_delete BEFORE DELETE ON notice_task_events BEGIN SELECT RAISE(ABORT, 'notice_task_events is append-only'); END;
"""

POST_LEVEL = 2          # Manager and up post notices
CLOSING_FROM = 22       # a shift ending at 22:00 or later closes

DEEP_CLEAN_AREAS = [
    "Fryer: drain, boil out, fresh oil",
    "Hood filters: soak and degrease",
    "Walk-in: empty shelves, wipe down, date-check",
    "Dish pit: delime the machine, scrub the floor drain",
    "Line: pull equipment, clean behind and under",
]


def ensure(db):
    db.executescript(SCHEMA)


def plan(schedule, areas):
    """schedule: {day: [(member, start_hour, end_hour), ...]} in day order. One area per night, to a closer,
    rotating so the same person isn't picked twice while someone else closing hasn't had one yet."""
    days = [d for d in sorted(schedule) if any(e >= CLOSING_FROM for _, _, e in schedule[d])]
    if not days:
        raise ValueError("No one closes on the schedule that week")
    counts, out = {}, []
    for i, area in enumerate(areas):
        day = days[i % len(days)]
        closers = sorted((m for m, _, e in schedule[day] if e >= CLOSING_FROM), key=lambda m: (counts.get(m, 0), m))
        who = closers[0]
        counts[who] = counts.get(who, 0) + 1
        out.append({"member": who, "area": area, "due_on": day})
    return out


def post(db, member_level, posted_by, kind, title, week_start, areas, schedule):
    if member_level < POST_LEVEL:
        raise PermissionError("Posting a notice needs Manager level")
    if not areas:
        raise ValueError("A notice needs at least one area")
    tasks = plan(schedule, areas)
    nid = db.execute("INSERT INTO notices(posted_by, kind, title, week_start, areas) VALUES (?,?,?,?,?)",
                     (posted_by, kind, title, week_start, json.dumps(areas))).lastrowid
    for t in tasks:
        tid = db.execute("INSERT INTO notice_tasks(notice_id, member, area, due_on) VALUES (?,?,?,?)",
                         (nid, t["member"], t["area"], t["due_on"])).lastrowid
        db.execute("INSERT INTO notice_task_events(task_id, status, actor) VALUES (?, 'open', ?)", (tid, posted_by))
    return nid


def _status(db, tid):
    r = db.execute("SELECT status FROM notice_task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    return r["status"] if r else "open"


def tasks_for(db, member):
    return [{**dict(r), "status": _status(db, r["id"])} for r in db.execute(
        "SELECT t.*, n.title FROM notice_tasks t JOIN notices n ON n.id=t.notice_id WHERE t.member=? ORDER BY t.due_on",
        (member,))]


def notices(db):
    out = []
    for n in db.execute("SELECT * FROM notices ORDER BY id DESC"):
        tasks = [{**dict(t), "status": _status(db, t["id"])}
                 for t in db.execute("SELECT * FROM notice_tasks WHERE notice_id=? ORDER BY due_on", (n["id"],))]
        out.append({**dict(n), "areas": json.loads(n["areas"]), "tasks": tasks})
    return out


def mark_done(db, task_id, actor, note=None):
    r = db.execute("SELECT * FROM notice_tasks WHERE id=?", (task_id,)).fetchone()
    if not r or r["member"] != actor:
        raise LookupError("That task is not on your list")
    if _status(db, task_id) == "done":
        raise ValueError("Already done")
    db.execute("INSERT INTO notice_task_events(task_id, status, actor, note) VALUES (?, 'done', ?, ?)", (task_id, actor, note))
