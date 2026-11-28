"""Chat history: every assistant and team-chat message, browsable by month, then by day and time, and findable.

Two sources, both append-only (never edited or deleted):
  assistant_messages   your questions to the assistant and its answers. Only you see your own.
  team_messages        the team channel (everyone on the roster) and one-to-one threads (only the two people in it).

Who sees what is decided here, on the server, before anything is searched:
  - assistant history: only your own, and only answers given at your level or below. If the account holder lowers
    your level, answers you got at the higher level are held back (counted, not shown).
  - team channel: everyone. Direct messages: only the two people in the thread. A manager or the owner can't read
    other people's direct messages through history or search.

Search runs on this computer and never asks a language model:
  1. words: every word you typed is in the message (same idea as Recall's search), newest first.
  2. meaning (optional): when an embedder is given (agent.vectors: Ollama's nomic-embed-text, the same local model
     the rules index uses), messages close in meaning are added after the word matches. Vectors are cached in
     chat_vectors, so each message is embedded once. PyTorch scores them in one step when it's installed.
Filters you can type: a month ("october", "oct 2026", "2026-10"), "from:marco", "in:team", "in:dm", "in:assistant".

Standard library only (torch and the embedder are optional).
"""
import calendar
import json
import math
import re
from datetime import date

from hiwr_access import level as level_of

try:
    import torch
except ImportError:          # the word search works without it
    torch = None

SOURCES = ("assistant", "team", "dm")
MEANING_MIN = 0.55           # cosine score a meaning-only match needs (nomic-embed-text scale)


def ensure(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS assistant_messages (
            id INTEGER PRIMARY KEY,
            at TEXT NOT NULL,
            member TEXT NOT NULL,
            level INTEGER NOT NULL,          -- the asker's level when the answer was given
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            text TEXT NOT NULL,
            backend TEXT
        );
        CREATE INDEX IF NOT EXISTS assistant_messages_member ON assistant_messages(member, at);
        CREATE TRIGGER IF NOT EXISTS assistant_messages_no_update BEFORE UPDATE ON assistant_messages
            BEGIN SELECT RAISE(ABORT, 'assistant_messages is append-only'); END;
        CREATE TRIGGER IF NOT EXISTS assistant_messages_no_delete BEFORE DELETE ON assistant_messages
            BEGIN SELECT RAISE(ABORT, 'assistant_messages is append-only'); END;
        -- derived data (can be rebuilt from the messages), so it isn't append-only
        CREATE TABLE IF NOT EXISTS chat_vectors (
            key TEXT NOT NULL,               -- 'a:<id>' or 't:<id>'
            model TEXT NOT NULL,
            vec TEXT NOT NULL,
            PRIMARY KEY (key, model)
        );
    """)
    db.commit()


def record_assistant(db, member, level, question, answer, backend, at):
    """One question and its answer, kept on the record. `at` is 'YYYY-MM-DDTHH:MM'."""
    for role, text in (("user", question), ("assistant", answer)):
        db.execute("INSERT INTO assistant_messages(at, member, level, role, text, backend) VALUES (?,?,?,?,?,?)",
                   (at, member, level, role, text, backend if role == "assistant" else None))
    db.commit()


def _pair(channel):
    """'dm:a|b' -> ('a', 'b'); anything else -> None."""
    if not channel.startswith("dm:") or channel.count("|") != 1:
        return None
    return tuple(channel[3:].split("|"))


def _at(s):
    """'2026-11-20 14:05:00' and '2026-11-20T14:05' -> '2026-11-20T14:05'."""
    return s.replace(" ", "T")[:16]


def messages(db, me, level, name_of=lambda m: m):
    """Everything `me` may see, oldest first. Also returns how many assistant answers were held back."""
    out, held = [], 0
    for r in db.execute("SELECT * FROM assistant_messages WHERE member=? ORDER BY at, id", (me,)):
        if r["level"] > level:
            held += 1
            continue
        out.append({"key": f"a:{r['id']}", "source": "assistant", "at": _at(r["at"]), "from": me if r["role"] == "user" else "assistant",
                    "who": "You" if r["role"] == "user" else "Assistant", "mine": r["role"] == "user",
                    "name": name_of(me) if r["role"] == "user" else "Assistant",
                    "where": "Assistant", "with": None, "text": r["text"]})
    for r in db.execute("SELECT * FROM team_messages ORDER BY at, id"):
        ch = r["channel"]
        if ch == "team":
            src, where, other = "team", "Whole team", None
        else:
            pair = _pair(ch)
            if not pair or me not in pair:
                continue                     # someone else's direct messages: never read, never searched
            other = pair[1] if pair[0] == me else pair[0]
            src, where = "dm", name_of(other)
        out.append({"key": f"t:{r['id']}", "source": src, "at": _at(r["at"]), "from": r["sender"],
                    "who": "You" if r["sender"] == me else name_of(r["sender"]), "mine": r["sender"] == me,
                    "name": name_of(r["sender"]),
                    "where": where, "with": other, "text": r["text"]})
    # same minute: keep the order they were written (a question before its answer)
    out.sort(key=lambda m: (m["at"], m["key"][0], int(m["key"][2:])))
    return out, held


# ---------- browsing: month, then day and time ----------

def month_label(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{calendar.month_name[m]} {y}"


def day_label(d):
    x = date.fromisoformat(d)
    return f"{calendar.day_name[x.weekday()]}, {calendar.month_abbr[x.month]} {x.day}"


def months(msgs):
    """[{'month': '2026-11', 'label': 'November 2026', 'count', 'days', 'by_source'}], newest month first."""
    out = {}
    for m in msgs:
        ym = m["at"][:7]
        e = out.setdefault(ym, {"month": ym, "label": month_label(ym), "count": 0, "days": set(),
                                "by_source": {s: 0 for s in SOURCES}})
        e["count"] += 1
        e["days"].add(m["at"][:10])
        e["by_source"][m["source"]] += 1
    return [{**e, "days": len(e["days"])} for _, e in sorted(out.items(), reverse=True)]


def by_day(msgs, month):
    """The month's messages grouped by day, oldest day first, each with its time 'HH:MM'."""
    days = {}
    for m in msgs:
        if m["at"][:7] == month:
            days.setdefault(m["at"][:10], []).append({**m, "time": m["at"][11:16]})
    return [{"day": d, "label": day_label(d), "messages": ms} for d, ms in sorted(days.items())]


# ---------- search ----------

_WORD = re.compile(r"[a-z0-9áéíóúñü]+(?:'[a-z]+)?")
_STOP = set("a an and are as at be by can for from has have in is it its of on or so that the to was we what when "
            "where which who will with you your i my me our do did any all about find show".split())
_MONTHS = {n.lower(): i for i, n in enumerate(calendar.month_name) if n}
_MONTHS.update({n.lower(): i for i, n in enumerate(calendar.month_abbr) if n})
_FILTER = re.compile(r"\b(from|in):(\S+)", re.I)
_YM = re.compile(r"\b(20\d\d)-(0[1-9]|1[0-2])\b")
_YEAR = re.compile(r"\b(20\d\d)\b")


def _stem(w):
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("ing"):
        return w[:-3]
    if len(w) > 3 and w.endswith("ed"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def words(text):
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]


def parse_query(q, today):
    """Pulls the filters out of what was typed. Returns (words, filters)."""
    q = (q or "").strip()
    f = {"month": None, "from": None, "in": None}
    for k, v in _FILTER.findall(q):
        f[k.lower()] = v.lower()
    q = _FILTER.sub(" ", q)
    m = _YM.search(q)
    if m:
        f["month"] = f"{m.group(1)}-{m.group(2)}"
        q = _YM.sub(" ", q)
    else:
        found = [w for w in re.findall(r"[a-z]+", q.lower()) if w in _MONTHS and len(w) >= 3]
        # "may" and "march" are also everyday words; only treat a month name as a filter when it's the
        # full name or a 3-letter abbreviation, and leave it in the words too if nothing else was typed
        if found:
            mon = _MONTHS[found[0]]
            y = _YEAR.search(q)
            year = int(y.group(1)) if y else (today.year if mon <= today.month else today.year - 1)
            f["month"] = f"{year}-{mon:02d}"
            q = re.sub(rf"\b{found[0]}\b", " ", q, flags=re.I)
            q = _YEAR.sub(" ", q)
    if f["in"] in ("direct", "dms", "private"):
        f["in"] = "dm"
    return words(q), f


def _from_matches(m, who):
    """from:marco matches Marco's messages (yours too, if you're Marco); from:me matches yours."""
    if who in ("me", "you", "yo"):
        return m["mine"]
    return m["name"].lower().startswith(who) or any(p.startswith(who) for p in m["from"].lower().split("_"))


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


def _scores(qv, vecs):
    """Cosine of the query against every message vector (all length 1). One matrix step with torch."""
    if not vecs:
        return []
    if torch is not None:
        return (torch.tensor(vecs, dtype=torch.float32) @ torch.tensor(qv, dtype=torch.float32)).tolist()
    return [sum(a * b for a, b in zip(qv, v)) for v in vecs]


def _vectors(db, embedder, msgs):
    """The cached vector for each message, embedding only the ones not seen before."""
    model = f"{embedder.name}:{embedder.model}"
    have = {r["key"]: json.loads(r["vec"]) for r in db.execute("SELECT key, vec FROM chat_vectors WHERE model=?", (model,))}
    todo = [m for m in msgs if m["key"] not in have]
    for i in range(0, len(todo), 64):
        batch = todo[i:i + 64]
        for m, v in zip(batch, embedder.embed([x["text"] for x in batch], "document")):
            have[m["key"]] = _norm(v)
            db.execute("INSERT OR REPLACE INTO chat_vectors(key, model, vec) VALUES (?,?,?)", (m["key"], model, json.dumps(have[m["key"]])))
    if todo:
        db.commit()
    return [have[m["key"]] for m in msgs]


def search(db, me, level, q, today, name_of=lambda m: m, embedder=None, limit=40):
    """{'hits': [...], 'how': 'words' | 'words+meaning', 'filters', 'note', 'held'}.

    Each hit is a message with its month and day, so the app can jump to it in the history."""
    today = date.fromisoformat(today) if isinstance(today, str) else today
    ws, f = parse_query(q, today)
    allm, held = messages(db, me, level, name_of)
    pool = [m for m in allm
            if (not f["month"] or m["at"][:7] == f["month"])
            and (not f["in"] or m["source"] == f["in"])
            and (not f["from"] or _from_matches(m, f["from"]))]
    note, how = None, "words"
    if not ws:
        hits = [dict(m, match="filter") for m in pool]
        hits.reverse()
        return {"hits": [_hit(h) for h in hits[:limit]], "total": len(hits), "how": how, "filters": f, "note": note, "held": held}
    need = set(ws)
    word_hits = [dict(m, match="words") for m in reversed(pool) if need <= set(words(m["text"]))]
    hits = list(word_hits)
    if embedder is not None and pool and getattr(embedder, "name", "") != "hash":
        try:
            vecs = _vectors(db, embedder, pool)
            qv = _norm(embedder.embed([" ".join(ws)], "query")[0])
            seen = {h["key"] for h in hits}
            close = sorted(((s, m) for s, m in zip(_scores(qv, vecs), pool) if s >= MEANING_MIN and m["key"] not in seen),
                           key=lambda sm: -sm[0])
            hits += [dict(m, match="meaning", score=round(s, 3)) for s, m in close]
            how = "words+meaning"
        except Exception as e:  # noqa: BLE001 - the local model being down never breaks finding by words
            note = f"Meaning search is off right now ({type(e).__name__}); showing word matches only."
    return {"hits": [_hit(h) for h in hits[:limit]], "total": len(hits), "how": how, "filters": f, "note": note, "held": held}


def _hit(m):
    return {**m, "month": m["at"][:7], "day": m["at"][:10], "time": m["at"][11:16], "day_label": day_label(m["at"][:10])}


# ---------- sample history for the test restaurant ----------

SAMPLE_TEAM = [
    # (at, sender, to, text). to: 'team' or a member id. People and events are sample data.
    ("2026-09-04T15:10", "chef_marco", "team", "New fryer oil schedule: change it every 3 days, or sooner if it smokes."),
    ("2026-09-04T15:22", "fry_rosa", "team", "Entendido. I'll log each change on the prep screen."),
    ("2026-09-12T10:05", "manager_jo", "owner_alex", "Fish delivery came short again, 6 lb under. Ticket is open."),
    ("2026-09-12T10:18", "owner_alex", "manager_jo", "Thanks. Keep it in anticipated costs until we both approve the credit."),
    ("2026-09-19T16:40", "chef_marco", "fry_rosa", "Can you cover Saturday close? Luis is out."),
    ("2026-09-19T16:52", "fry_rosa", "chef_marco", "Sí, puedo. Until 11?"),
    ("2026-10-03T09:30", "stocker_tim", "team", "Walk-in cooler read 43F this morning. Moved the dairy to the reach-in."),
    ("2026-10-03T09:41", "chef_marco", "team", "Good call. I logged the corrective action. Tech coming at noon."),
    ("2026-10-15T14:00", "manager_jo", "team", "Health inspection next Tuesday. Labels and dates on everything, please."),
    ("2026-10-22T17:15", "fry_rosa", "chef_marco", "The tomato in the salsa: a guest asked if it's in the chips. Told them no."),
    ("2026-10-22T17:20", "chef_marco", "fry_rosa", "Right. Chips are just corn, oil, salt. Salsa has tomato."),
    ("2026-11-06T11:45", "owner_alex", "team", "Big party of 40 on the 20th. Extra fryer batches after 6."),
    ("2026-11-14T13:05", "stocker_tim", "manager_jo", "Two cases of tortillas came damaged. Took photos for the shipment ticket."),
    ("2026-11-18T15:30", "chef_marco", "team", "Fryer oil changed today, next change Friday."),
    ("2026-11-20T15:50", "fry_rosa", "team", "Chips are low. Starting 3 batches now."),
]

SAMPLE_ASSISTANT = [
    # (at, member, question, answer)
    ("2026-09-12T10:30", "owner_alex", "How much did the fish short shipment cost us?",
     "The short shipment was 6 lb under. It's in anticipated costs, PENDING - requires double approval for a credit."),
    ("2026-10-03T09:50", "chef_marco", "What do I log when the walk-in is too warm?",
     "Log the reading, the time, what you moved and where, and who fixed it. That's the corrective action record."),
    ("2026-10-15T14:10", "chef_marco", "What do inspectors look at first?",
     "Cold holding temps, date labels, hand sinks, and the corrective action logs."),
    ("2026-11-06T12:00", "owner_alex", "Will we be short on the fryer on the 20th?",
     "With the party of 40 the fryer runs about 45% over plan after 6 PM. Plan extra batches from 5:30."),
    ("2026-11-20T16:05", "fry_rosa", "¿A qué hora salgo hoy?", "Hoy estás de 14:00 a 22:00."),
]


def seed_sample(db):
    """Fills a few months of history on the test restaurant, so browsing and search have something to show."""
    if db.execute("SELECT 1 FROM team_messages LIMIT 1").fetchone() or db.execute("SELECT 1 FROM assistant_messages LIMIT 1").fetchone():
        return
    ids = {r["id"] for r in db.execute("SELECT id FROM members")}
    for at, sender, to, text in SAMPLE_TEAM:
        if sender not in ids or (to != "team" and to not in ids):
            continue
        ch = "team" if to == "team" else "dm:" + "|".join(sorted([sender, to]))
        db.execute("INSERT INTO team_messages(at, sender, channel, text) VALUES (?,?,?,?)", (at.replace("T", " ") + ":00", sender, ch, text))
    for at, mid, q, a in SAMPLE_ASSISTANT:
        if mid in ids:
            record_assistant(db, mid, level_of(db, mid), q, a, "local", at)
    db.commit()
