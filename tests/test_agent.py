"""Agent runner: the rules that must hold in code, whatever the model says.

A scripted fake model stands in for Ollama and Claude, so these run offline and cost nothing.
"""
import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT))

from agent import config as config_mod, privacy  # noqa: E402
from agent.backends import OllamaBackend, ClaudeBackend, BackendUnavailable  # noqa: E402
from agent.router import route  # noqa: E402
from agent.runner import Agent, sample_db  # noqa: E402
from agent.tools import TABLE_LEVEL, PENDING  # noqa: E402


class FakeModel:
    """Plays back a script of tool calls, then a reply. Records everything it was shown."""

    def __init__(self, name, steps, reply="Done.", model="fake"):
        self.name, self.steps, self.reply, self.model = name, list(steps), reply, model
        self.seen = []

    def turn(self, system, history, user_text, tools, execute):
        self.seen += [system, json.dumps(history), user_text]
        self.tool_names = [t["name"] for t in tools]
        self.results = []
        for name, args in self.steps:
            r = execute(name, args)
            self.results.append(r)
            self.seen.append(json.dumps(r))
        return self.reply(self) if callable(self.reply) else self.reply


@pytest.fixture
def sample(tmp_path):
    db, now = sample_db(tmp_path / "s.db")
    db.execute("INSERT INTO orders(id, workflow_id, fulfillment) VALUES (501, 1, 'pickup')")
    db.execute("INSERT INTO order_items(order_id, item, qty) VALUES (501, 'Fish tacos', 2)")
    db.executescript((ROOT / "agent" / "agent_schema.sql").read_text())
    db.execute("INSERT INTO order_payments(order_id, card_last4) VALUES (501, '4242')")
    return db, now, tmp_path


def make(sample, member, local=(), cloud=(), mode="hybrid", reply="Done.", **cfg_over):
    db, now, tmp = sample
    cfg = config_mod.load("/nonexistent", {"mode": mode, "data": {"db": str(tmp / "s.db"), "folder": str(tmp / "data")},
                                           **cfg_over})
    be = {"local": FakeModel("local", local, reply), "cloud": FakeModel("cloud", cloud, reply)}
    return Agent(db, cfg, member, backends=be, clock=lambda: now, db_path=tmp / "s.db"), be


# ---------- routing ----------

def test_hybrid_routes_heavy_work_to_cloud_and_routine_to_local():
    cfg = config_mod.load("/nonexistent")
    assert route(cfg, "Can Rosa swap her Saturday shift?")[0] == "local"
    assert route(cfg, "What does next week look like for payroll and labor?")[0] == "cloud"
    assert route(cfg, "Why do we keep getting short shipments? Any pattern?")[0] == "cloud"
    assert route(cfg, "/local what does next week look like")[0] == "local"
    assert route(config_mod.load("/nonexistent", {"mode": "local"}), "next week forecast")[0] == "local"


def test_num_ctx_below_16k_is_refused():
    with pytest.raises(ValueError):
        config_mod.load("/nonexistent", {"local": {"num_ctx": 2048}})


def test_cloud_down_falls_back_to_local_in_hybrid(sample):
    agent, be = make(sample, "owner_alex", reply="Local answer.")

    class Down:
        model = "x"

        def turn(self, *a):
            raise BackendUnavailable("no network")
    agent.backends["cloud"] = Down()
    out = agent.ask("What does next week look like?")
    assert "Local answer." in out and "wasn't reachable" in out
    assert agent.last_route["backend"] == "local"


# ---------- privacy ----------

def test_cloud_never_sees_names_phones_or_cards(sample):
    steps = [("query_db", {"sql": "SELECT name, phone FROM candidates"}),
             ("query_db", {"sql": "SELECT member, role FROM staff"})]
    agent, be = make(sample, "owner_alex", cloud=steps, reply="Applicant 1 looks good; ask Employee H.")
    out = agent.ask("/cloud Marcus Bell called from 910-555-0161, card 4111 1111 1111 1234. Plan next week with fry_rosa.")
    sent = "\n".join(be["cloud"].seen)
    for secret in ("Marcus", "Bell", "910-555-0161", "4111 1111 1111 1234", "fry_rosa", "Rosa", "Owens", "Tia"):
        assert secret not in sent, secret
    assert "Applicant 1" in sent and "****1234" in sent
    assert "Bell" not in out and "Marcus" in out          # mapped back to a first name only


def test_cloud_tool_calls_are_mapped_back_before_running(sample):
    rosa_label = next(p.label for p in privacy.Pseudonymizer(privacy.people_from_db(sample[0])).people if p.ref == "fry_rosa")
    steps = [("open_ticket", {"type": "SHIPMENT", "subject": f"short fish, reported by {rosa_label}",
                              "fields": {"employee_ref": rosa_label}})]
    agent, be = make(sample, "owner_alex", cloud=steps)
    agent.ask("/cloud log it")
    tid = be["cloud"].results[0]["ticket_id"]
    detail = sample[0].execute("SELECT detail FROM ticket_events WHERE ticket_id=? AND event='opened_fields'", (tid,)).fetchone()[0]
    assert json.loads(detail)["employee_ref"] == "fry_rosa"


def test_last_names_and_cards_are_scrubbed_even_locally(sample):
    agent, be = make(sample, "owner_alex", local=[("query_db", {"sql": "SELECT name FROM candidates"})],
                     reply="Call Marcus Bell, card 4111111111111234.")
    out = agent.ask("who applied?")
    assert "Bell" not in json.dumps(be["local"].results)
    assert "Marcus B." in out and "4111111111111234" not in out and "****1234" in out


# ---------- access levels ----------

def test_staff_cannot_read_payroll_or_money_tables(sample):
    agent, be = make(sample, "fry_rosa", local=[("query_db", {"sql": "SELECT * FROM staff"}),
                                                ("query_db", {"sql": "SELECT * FROM money_actions"}),
                                                ("query_db", {"sql": "SELECT s.hourly_cents FROM tickets t, staff s"})])
    agent.ask("how much does everyone make?")
    for r in be["local"].results:
        assert "refused" in r and "rows" not in r


def test_financial_block_hidden_below_finance(sample):
    db = sample[0]
    db.execute("UPDATE tickets SET financial=? WHERE id='SHP-0001'", (json.dumps({"variance_amount": 55}),))
    steps = [("get_ticket", {"ticket_id": "SHP-0001"}), ("query_db", {"sql": "SELECT id, financial FROM tickets"}),
             ("list_tickets", {})]
    agent, be = make(sample, "chef_marco", local=steps)          # Manager, level 2
    agent.ask("ticket status?")
    got, q, lst = be["local"].results
    assert got["financial"] == "hidden at this level"
    assert q["rows"][0][1] is None
    assert "dollars_at_stake" not in lst["tickets"][0]
    agent2, be2 = make(sample, "manager_jo", local=steps)        # Finance, level 3
    agent2.ask("ticket status?")
    assert be2["local"].results[0]["financial"] == {"variance_amount": 55}


def test_tools_above_level_are_not_even_offered(sample):
    agent, be = make(sample, "fry_rosa")
    agent.ask("hi")
    assert "propose_money_action" not in be["local"].tool_names
    assert "quickbooks_read" not in be["local"].tool_names
    out = agent.tools.call("propose_money_action", {"kind": "refund", "amount": 5, "description": "x"})
    assert "refused" in out


def test_query_db_is_read_only(sample):
    agent, _ = make(sample, "owner_alex")
    for sql in ("DELETE FROM tickets", "UPDATE tickets SET status='closed'", "INSERT INTO approvals VALUES (1,1,'x','approve',1)",
                "PRAGMA table_info(tickets)", "ATTACH DATABASE 'x.db' AS x", "SELECT 1; DELETE FROM tickets"):
        assert "refused" in agent.tools.call("query_db", {"sql": sql}), sql
    assert sample[0].execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 1


def test_every_table_has_a_level_or_defaults_to_manager(sample):
    tables = {r[0] for r in sample[0].execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert TABLE_LEVEL["staff"] == 3 and TABLE_LEVEL["order_payments"] == 4
    agent, _ = make(sample, "lead_x")                             # unknown member: lowest level
    assert agent.tools.level == 0
    assert "refused" in agent.tools.call("query_db", {"sql": "SELECT * FROM contacts"})
    assert len(tables) > 50


def test_files_need_their_level_and_stay_in_the_folder(sample):
    data = sample[2] / "data"
    data.mkdir()
    (data / "payroll_week47.csv").write_text("member,hours\nfry_rosa,41\n")
    (data / "fee_policy.txt").write_text("No-show fee: 25 USD")
    agent, _ = make(sample, "fry_rosa")
    assert "refused" in agent.tools.call("read_file", {"path": "payroll_week47.csv"})
    assert "25 USD" in agent.tools.call("read_file", {"path": "fee_policy.txt"})["text"]
    assert "refused" in agent.tools.call("read_file", {"path": "../s.db"})
    listing = agent.tools.call("list_files", {})["files"]
    assert {f["path"]: f["readable"] for f in listing} == {"fee_policy.txt": True, "payroll_week47.csv": False}


# ---------- approvals ----------

def test_no_tool_can_approve_send_pay_or_write_quickbooks(sample):
    agent, be = make(sample, "owner_alex")
    agent.ask("hi")
    for name in be["local"].tool_names:
        assert not any(w in name for w in ("approve", "send", "pay", "post", "execute", "write_qb", "delete"))


def test_money_proposals_stay_pending_and_the_reply_says_so(sample):
    steps = [("propose_money_action", {"kind": "refund", "amount": 18.5, "description": "Refund 2 fish tacos",
                                       "ticket_id": "SHP-0001"}),
             ("propose_money_action", {"kind": "purchase_order", "amount": 220, "description": "Re-order white fish"})]
    agent, be = make(sample, "chef_marco", local=steps, reply="Refund is queued.")
    out = agent.ask("refund the fish tacos and reorder")
    r1, r2 = be["local"].results
    assert r1["status"].startswith("PENDING") and r2["status"] == PENDING
    assert set(r1["approvers"]) == {"manager_jo", "accounting_sue"}
    assert out.count(PENDING) == 2 and "Jo" in out and "Sue" in out


def test_cannot_close_ticket_with_pending_money(sample):
    steps = [("propose_money_action", {"kind": "refund", "amount": 10, "description": "x", "ticket_id": "SHP-0001"}),
             ("update_ticket_status", {"ticket_id": "SHP-0001", "status": "closed"})]
    agent, be = make(sample, "chef_marco", local=steps)
    agent.ask("close it")
    assert "refused" in be["local"].results[1]
    assert sample[0].execute("SELECT status FROM tickets WHERE id='SHP-0001'").fetchone()[0] != "closed"


def test_staff_financial_block_is_dropped_on_open(sample):
    agent, be = make(sample, "fry_rosa", local=[("open_ticket", {"type": "SHIPMENT", "subject": "short lettuce",
                                                                 "financial": {"variance_amount": 999}})])
    agent.ask("lettuce came short")
    r = be["local"].results[0]
    assert "Finance level" in r["note"]
    assert sample[0].execute("SELECT financial FROM tickets WHERE id=?", (r["ticket_id"],)).fetchone()[0] is None


def test_quickbooks_is_read_only_and_gated_on_connected(sample, tmp_path):
    agent, _ = make(sample, "manager_jo")
    assert agent.tools.call("quickbooks_read", {})["connected"] is False
    qb = tmp_path / "qb.csv"
    qb.write_text("account,amount\nFood cost,4100\n")
    agent2, _ = make(sample, "manager_jo", quickbooks={"connected": True, "export_file": str(qb)})
    r = agent2.tools.call("quickbooks_read", {})
    assert r["read_only"] and r["rows"][0]["amount"] == "4100"


# ---------- verification, live person, history ----------

def test_sale_needs_order_and_last4_and_never_says_which_part_failed(sample):
    agent, _ = make(sample, "fry_rosa")
    assert "refused" in agent.tools.call("get_order", {"order_id": 501})
    bad = agent.tools.call("verify_sale", {"order_id": 501, "card_last4": "1111"})
    assert bad == {"verified": False, "message": "could not verify"}
    assert agent.tools.call("verify_sale", {"order_id": 999, "card_last4": "4242"}) == bad
    assert agent.tools.call("verify_sale", {"order_id": 501, "card_last4": "4242"})["verified"]
    assert agent.tools.call("get_order", {"order_id": 501})["items"][0]["item"] == "Fish tacos"


def test_verification_locks_after_three_misses(sample):
    agent, _ = make(sample, "fry_rosa")
    for _ in range(3):
        agent.tools.call("verify_sale", {"order_id": 501, "card_last4": "0000"})
    assert "refused" in agent.tools.call("verify_sale", {"order_id": 501, "card_last4": "4242"})


def test_live_person_is_queued_recorded_on_ticket_and_contacts_not_invented(sample):
    agent, _ = make(sample, "fry_rosa")
    r = agent.tools.call("request_live_person", {"channel": "callback", "route_to": "on_duty_manager",
                                                 "contact": "910-555-0100", "ticket_id": "SHP-0001"})
    assert r["status"].startswith("queued")
    assert set(r["business_contacts"]["missing"]) == {"phone", "text_number", "hours", "on_duty_manager"}
    ev = sample[0].execute("SELECT event FROM ticket_events WHERE ticket_id='SHP-0001' ORDER BY id DESC").fetchone()[0]
    assert ev == "live_person"


def test_history_tables_are_append_only(sample):
    agent, _ = make(sample, "owner_alex")
    agent.ask("hi")
    agent.tools.call("request_live_person", {"channel": "text", "route_to": "owner"})
    agent.tools.call("propose_money_action", {"kind": "reward", "description": "shout-out"})
    for t in ("agent_log", "agent_handoffs", "agent_proposals", "order_payments"):
        with pytest.raises(Exception):
            sample[0].execute(f"DELETE FROM {t}")
    row = sample[0].execute("SELECT requester, backend FROM agent_log").fetchone()
    assert tuple(row) == ("owner_alex", "local")


def test_drafts_never_overwrite_and_are_scrubbed(sample):
    agent, _ = make(sample, "owner_alex")
    a = agent.tools.call("write_draft", {"filename": "note.txt", "content": "Hi Marcus Bell"})
    b = agent.tools.call("write_draft", {"filename": "../note.txt", "content": "second"})
    assert a["saved"] == "drafts/note.txt" and b["saved"] == "drafts/note-1.txt" and a["sent"] is False
    assert (sample[2] / "data" / "drafts" / "note.txt").read_text() == "Hi Marcus B."


def test_approved_script_runs_on_finance_only(sample):
    agent, _ = make(sample, "manager_jo")
    r = agent.tools.call("run_script", {"name": "ticket_exposure", "args": ["2026-11-20"]})
    assert r["exit_code"] == 0 and json.loads(r["output"])["by_type"]["SHIPMENT"]["open"] == 1
    agent2, _ = make(sample, "chef_marco")
    assert "refused" in agent2.tools.call("run_script", {"name": "ticket_exposure"})


# ---------- backends speak the right wire format ----------

def test_ollama_backend_sets_num_ctx_and_runs_tools():
    cfg = config_mod.load("/nonexistent")
    sent = []

    def post(url, payload):
        sent.append(json.loads(json.dumps(payload)))
        if len(sent) == 1:
            return {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "list_tickets", "arguments": {"status": "open"}}}]}}
        return {"message": {"role": "assistant", "content": "One open ticket."}}

    calls = []
    out = OllamaBackend(cfg, post=post).turn("sys", [], "hi", [{"name": "list_tickets", "description": "d",
                                                                 "parameters": {"type": "object"}}],
                                             lambda n, a: calls.append((n, a)) or {"tickets": []})
    assert out == "One open ticket." and calls == [("list_tickets", {"status": "open"})]
    assert sent[0]["options"]["num_ctx"] >= 16384 and sent[1]["messages"][-1]["role"] == "tool"


def test_ollama_blank_answer_gets_one_nudge():
    """gpt-oss can stop after thinking with no answer: ask once, then give up with what came back."""
    cfg = config_mod.load("/nonexistent")
    answers = iter(["", "Nobody is short tonight."])
    sent = []

    def post(url, payload):
        sent.append(json.loads(json.dumps(payload)))
        return {"message": {"role": "assistant", "content": next(answers), "thinking": "..."}}

    assert OllamaBackend(cfg, post=post).turn("sys", [], "short?", [], None) == "Nobody is short tonight."
    assert sent[1]["messages"][-1] == {"role": "user", "content": "Write your answer to my question now."}

    always_blank = OllamaBackend(cfg, post=lambda u, p: {"message": {"role": "assistant", "content": ""}})
    assert always_blank.turn("sys", [], "short?", [], None) == ""      # the web app turns this into "ask again"


def test_claude_backend_request_shape():
    from types import SimpleNamespace as NS
    cfg = config_mod.load("/nonexistent")
    seen = []

    class Msgs:
        def create(self, **kw):
            seen.append({**kw, "messages": list(kw["messages"])})
            if len(seen) == 1:
                return NS(stop_reason="tool_use", content=[NS(type="tool_use", id="t1", name="list_tickets", input={})])
            return NS(stop_reason="end_turn", content=[NS(type="text", text="All clear.")])

    client = NS(beta=NS(messages=Msgs()))
    out = ClaudeBackend(cfg, client=client).turn("sys", [], "hi", [{"name": "list_tickets", "description": "d",
                                                                     "parameters": {"type": "object"}}],
                                                 lambda n, a: {"tickets": []})
    assert out == "All clear."
    kw = seen[0]
    assert kw["model"] == "claude-opus-5-5" and kw["thinking"] == {"type": "adaptive"}
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"} and kw["fallbacks"] == "default"
    assert seen[1]["messages"][-1]["content"][0]["type"] == "tool_result"
