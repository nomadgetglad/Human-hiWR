"""Runs the Human hiWR operations assistant.

    python -m agent --as owner_alex                 # chat, using agent/config.toml
    python -m agent --as chef_marco "what's open?"  # one question
    python -m agent --sample --as owner_alex        # try it on the sample restaurant
    python -m agent --as owner_alex --mode local    # override the mode for this run

What stays on this computer no matter the mode: the hiWR data, the access levels, the approval rules
and the map from labels back to real names. The cloud only ever sees labels.
"""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "hiwr"))

from hiwr_access import LEVEL_NAMES  # noqa: E402
from hiwr_contacts import connect  # noqa: E402

from . import config as config_mod, privacy  # noqa: E402
from .backends import OllamaBackend, ClaudeBackend, BackendUnavailable  # noqa: E402
from .router import route  # noqa: E402
from .tools import Toolbox, PENDING  # noqa: E402
from .vectors import make_index  # noqa: E402

CORE = (HERE / "prompts" / "core.md").read_text(encoding="utf-8")
METHODS_DIR = HERE / "prompts" / "methods"


def prepare_db(db):
    db.executescript((HERE / "agent_schema.sql").read_text(encoding="utf-8"))
    return db


class Agent:
    def __init__(self, db, cfg, requester, backends=None, clock=None, db_path=None, on_cloud_payload=None, rules=None):
        self.db, self.cfg, self.requester = prepare_db(db), cfg, requester
        self.people = privacy.people_from_db(db, cfg["privacy"]["extra_names"], cfg["privacy"]["hide_vendors"])
        kw = {"clock": clock} if clock else {}
        self.tools = Toolbox(db, cfg, requester, self.people, db_path=db_path, **kw)
        self.pz = privacy.Pseudonymizer(self.people)      # one per conversation, so labels stay stable
        self.backends = backends or {"local": OllamaBackend(cfg), "cloud": ClaudeBackend(cfg)}
        self.history = []                                  # [(user_text, reply)] as the person saw them
        self.on_cloud_payload = on_cloud_payload
        self.last_route = None
        # The rules index only shapes the prompt. Every check that matters lives in self.tools and the guards.
        self.rules = rules if rules is not None else (make_index(cfg) if cfg["rules"]["retrieval"] else None)

    def _first(self, member):
        for p in self.people:
            if p.ref == member:
                return p.first
        return member

    def _system(self, methods, text=""):
        parts = [CORE]
        if self.rules is not None:
            block, self.rule_hits = self.rules.context(text, self.tools.level, self.cfg["rules"]["top_k"], methods)
            parts.append(block)
        elif methods:
            self.rule_hits = []
            parts.append("# Methods loaded for this request")
            parts += [(METHODS_DIR / f"{m}.md").read_text(encoding="utf-8") for m in methods]
        return "\n\n".join(parts)

    def ask(self, text):
        backend, methods, why, text = route(self.cfg, text)
        lvl = self.tools.level
        header = (f"[Requester: {self._first(self.requester)} ({self.requester}), level {lvl} {LEVEL_NAMES[lvl]}. "
                  f"Now: {self.tools.clock()}]")
        user = f"{header}\n\n{text}"
        system = self._system(methods, text)
        self.tools.turn_pending, self.tools.calls = [], []
        self.tools.loaded_methods = set(methods)

        note, reply = None, None
        for attempt in (backend, "local"):
            try:
                reply = self._run(attempt, system, user)
                backend = attempt
                break
            except BackendUnavailable as e:
                can_fall_back = (attempt == "cloud" and self.cfg["mode"] == "hybrid"
                                 and self.cfg["hybrid"]["fall_back_to_local"])
                if not can_fall_back:
                    raise
                note = f"(The cloud model wasn't reachable, so this ran on the local model instead: {e})"
        reply = privacy.scrub(reply, self.people)
        reply = self._approval_guard(reply)
        if note:
            reply = f"{note}\n\n{reply}"
        self.last_route = {"backend": backend, "methods": methods, "why": why, "rules": getattr(self, "rule_hits", []),
                           "rules_note": self.rules.note if self.rules is not None else None}
        self.db.execute("INSERT INTO agent_log(requester, level, backend, model, route_why, tools, hidden) VALUES (?,?,?,?,?,?,?)",
                        (self.requester, lvl, backend, self.backends[backend].model, why,
                         json.dumps(self.tools.calls), self.pz.hidden_count))
        self.db.commit()
        self.history.append((text, reply))
        return reply

    def _run(self, backend, system, user):
        b = self.backends[backend]
        specs = self.tools.specs()
        if backend == "local":
            hist = [m for u, r in self.history for m in ({"role": "user", "content": u}, {"role": "assistant", "content": r})]
            return b.turn(system, hist, user, specs, self.tools.call)
        pz = self.pz
        hist = [m for u, r in self.history
                for m in ({"role": "user", "content": pz.hide(u)}, {"role": "assistant", "content": pz.hide(r)})]
        hidden_user = pz.hide(user)
        if self.on_cloud_payload:
            self.on_cloud_payload(hidden_user)

        def execute(name, args):
            result = self.tools.call(name, pz.restore_args(args))
            out = pz.hide_obj(result)
            if self.on_cloud_payload:
                self.on_cloud_payload(f"[tool {name}] " + json.dumps(out, default=str)[:2000])
            return out

        return pz.show(b.turn(pz.hide(system), hist, hidden_user, specs, execute))

    def _approval_guard(self, reply):
        """Every money or authority item filed this turn shows up as pending, with its approvers."""
        missing = [p for p in self.tools.turn_pending if p["ref"] not in reply or PENDING not in reply]
        if not missing:
            return reply
        lines = []
        for p in missing:
            who = p["approvers"] if isinstance(p["approvers"], str) else ", ".join(self._first(a) for a in p["approvers"])
            amt = f" {p['amount']:.2f} {p['currency']}" if p["amount"] is not None else ""
            lines.append(f"- {p['ref']} ({p['kind']}{amt}): {PENDING}. Approvers: {who}")
        return reply.rstrip() + "\n\nApprovals required:\n" + "\n".join(lines)


def sample_db(path):
    """The sample restaurant from ui/server/seed.py, saved to a file so scripts can read it."""
    sys.path.insert(0, str(ROOT / "ui" / "server"))
    import seed  # noqa: E402
    mem = seed.build()
    path = Path(path)
    if path.exists():
        path.unlink()
    disk = sqlite3.connect(path)
    mem.backup(disk)
    disk.close()
    db = connect(str(path))
    return db, f"{seed.TODAY}T16:00"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m agent", description="Human hiWR operations assistant")
    ap.add_argument("--as", dest="member", required=True, help="who is asking, e.g. owner_alex (sets the access level)")
    ap.add_argument("--mode", choices=config_mod.MODES, help="override the mode in config.toml")
    ap.add_argument("--config", help="path to a config.toml (default agent/config.toml)")
    ap.add_argument("--sample", action="store_true", help="use the sample restaurant instead of your data")
    ap.add_argument("--show-cloud", action="store_true", help="print exactly what is sent to the cloud")
    ap.add_argument("question", nargs="*", help="ask one question and exit")
    a = ap.parse_args(argv)

    cfg = config_mod.load(a.config, {"mode": a.mode} if a.mode else None)
    clock = None
    if a.sample:
        db_path = HERE / "sample.db"
        db, now = sample_db(db_path)
        clock = lambda: now  # noqa: E731
    else:
        db_path = Path(cfg["data"]["db"]).resolve()
        db = connect(str(db_path))
    show = (lambda t: print(f"\n--- sent to cloud ---\n{t}\n---------------------", file=sys.stderr)) if a.show_cloud else None
    agent = Agent(db, cfg, a.member, clock=clock, db_path=db_path, on_cloud_payload=show)
    lvl = agent.tools.level
    print(f"Human hiWR assistant. Mode: {cfg['mode']}. You are {a.member}, level {lvl} {LEVEL_NAMES[lvl]}.", file=sys.stderr)

    def one(q):
        try:
            print(agent.ask(q))
        except BackendUnavailable as e:
            print(f"Couldn't reach the model: {e}")
        r = agent.last_route
        if r:
            print(f"  [{r['backend']} - {r['why']}]", file=sys.stderr)

    if a.question:
        one(" ".join(a.question))
        return
    print("Type a question. Start with /local or /cloud to choose where it runs. Ctrl+C or 'exit' to quit.", file=sys.stderr)
    while True:
        try:
            q = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if q.lower() in ("exit", "quit"):
            break
        if q:
            one(q)
