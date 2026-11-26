"""Year-end tax forms (W-2) posted by Finance, each one visible only to its employee and to Finance.

Posting needs Finance level (payroll data). Each employee sees only their own form. Every opening is logged,
append-only. Forms carry first names and amounts only: no Social Security numbers, no last names, no addresses.
The amounts in the demo are sample data. Standard library only.
"""
import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS tax_documents (
    id          INTEGER PRIMARY KEY,
    member      TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- W-2
    year        INTEGER NOT NULL,
    posted_by   TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT (datetime('now')),
    data        TEXT NOT NULL,          -- JSON boxes
    UNIQUE (member, kind, year)
);
CREATE TABLE IF NOT EXISTS tax_document_views (   -- append-only access log
    id          INTEGER PRIMARY KEY,
    doc_id      INTEGER NOT NULL REFERENCES tax_documents(id),
    viewer      TEXT NOT NULL,
    at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TRIGGER IF NOT EXISTS tax_documents_no_update BEFORE UPDATE ON tax_documents BEGIN SELECT RAISE(ABORT, 'tax_documents is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_documents_no_delete BEFORE DELETE ON tax_documents BEGIN SELECT RAISE(ABORT, 'tax_documents is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_document_views_no_update BEFORE UPDATE ON tax_document_views BEGIN SELECT RAISE(ABORT, 'tax_document_views is append-only'); END;
CREATE TRIGGER IF NOT EXISTS tax_document_views_no_delete BEFORE DELETE ON tax_document_views BEGIN SELECT RAISE(ABORT, 'tax_document_views is append-only'); END;
"""

POST_LEVEL = 3          # Finance and up: payroll data
SS_RATE, MEDICARE_RATE, FED_RATE = 0.062, 0.0145, 0.10


def ensure(db):
    db.executescript(SCHEMA)


def w2_boxes(wages_cents, employer="Sample restaurant (demo)"):
    w = int(wages_cents)
    return {"employer": employer, "box1_wages": w, "box2_federal_withheld": round(w * FED_RATE),
            "box3_ss_wages": w, "box4_ss_withheld": round(w * SS_RATE),
            "box5_medicare_wages": w, "box6_medicare_withheld": round(w * MEDICARE_RATE)}


def check_post(db, poster_level, year, wages):
    """wages: {member: cents}. Returns what would be posted; writes nothing."""
    if poster_level < POST_LEVEL:
        raise PermissionError("Posting W-2s needs Finance level")
    year = int(year)
    new = {m: c for m, c in wages.items()
           if not db.execute("SELECT 1 FROM tax_documents WHERE member=? AND kind='W-2' AND year=?", (m, year)).fetchone()}
    if not new:
        raise ValueError(f"W-2s for {year} are already posted")
    return year, new


def post_w2s(db, poster, poster_level, year, wages):
    year, new = check_post(db, poster_level, year, wages)
    for m, cents in new.items():
        db.execute("INSERT INTO tax_documents(member, kind, year, posted_by, data) VALUES (?, 'W-2', ?, ?, ?)",
                   (m, year, poster, json.dumps(w2_boxes(cents))))
    return len(new)


def my_documents(db, member):
    return [dict(r) for r in db.execute("SELECT id, kind, year, at FROM tax_documents WHERE member=? ORDER BY year DESC", (member,))]


def posted(db, viewer_level):
    """Finance view: who has which form, and how many times it was opened. No amounts."""
    if viewer_level < POST_LEVEL:
        raise PermissionError("Needs Finance level")
    return [{**dict(r), "views": db.execute("SELECT COUNT(*) FROM tax_document_views WHERE doc_id=?", (r["id"],)).fetchone()[0]}
            for r in db.execute("SELECT id, member, kind, year, posted_by, at FROM tax_documents ORDER BY year DESC, member")]


def open_document(db, viewer, viewer_level, doc_id):
    r = db.execute("SELECT * FROM tax_documents WHERE id=?", (doc_id,)).fetchone()
    if not r:
        raise LookupError("No such document")
    if r["member"] != viewer and viewer_level < POST_LEVEL:
        raise PermissionError("You can only open your own tax forms")
    db.execute("INSERT INTO tax_document_views(doc_id, viewer) VALUES (?,?)", (doc_id, viewer))
    return {"id": r["id"], "member": r["member"], "kind": r["kind"], "year": r["year"], "posted_by": r["posted_by"],
            "at": r["at"], **json.loads(r["data"])}
