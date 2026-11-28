"""Rules and app design as a vector index (agent/vectors.py, agent/knowledge/).

The search only decides what the model reads. These tests check that it finds the right chunks, and, more
important, that the hard rules don't depend on it: with no index, an empty index or a broken embedder, the
money, level and privacy rules still hold, because they live in code.
"""
import importlib
import json
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT))

from agent import config as config_mod  # noqa: E402
from agent.router import methods_for  # noqa: E402
from agent.tools import PENDING  # noqa: E402
from agent.vectors import (KINDS, EmbedderUnavailable, HashEmbedder, OllamaEmbedder, RuleIndex,  # noqa: E402
                           load_chunks, make_index)

from test_agent import make, sample  # noqa: E402,F401  (sample is a fixture)


@pytest.fixture(scope="module")
def index():
    return RuleIndex(embedder=HashEmbedder(), cache_dir=None).build()


def top(index, q, level=2, k=4):
    return [c.id for _, c in index.search(q, level, k, methods_for(q))]


# ---------- the chunks themselves ----------

def test_every_chunk_is_well_formed():
    chunks = load_chunks()
    ids = [c.id for c in chunks]
    assert len(ids) == len(set(ids)), "chunk ids must be unique"
    for c in chunks:
        assert c.kind in KINDS, c.id
        assert c.text and len(c.text) < 1200, f"{c.id} should be one small idea"
        assert c.tags, f"{c.id} needs tags"
        if c.kind in ("design", "policy"):
            assert c.status in ("built", "designed", "planned", "draft"), c.id


def test_every_method_paragraph_became_a_chunk():
    chunks = load_chunks()
    for p in (ROOT / "agent" / "prompts" / "methods").glob("*.md"):
        assert any(c.source.endswith(p.name) for c in chunks), p.name


def _resolve(ref):
    """'hiwr_contacts.approve' or 'agent.tools.Toolbox.call' -> the real function, or an error."""
    sys.path.insert(0, str(ROOT / "hiwr"))
    parts = ref.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:cut]))
        except ImportError:
            continue
        for name in parts[cut:]:
            obj = getattr(obj, name)
        return obj
    raise ImportError(ref)


def test_every_hard_rule_names_the_code_that_enforces_it():
    hard = [c for c in load_chunks() if c.kind == "hard"]
    assert len(hard) >= 8
    for c in hard:
        assert c.enforced_by, f"{c.id}: a hard rule with no code behind it is only a wish"
        for ref in c.enforced_by:
            assert _resolve(ref) is not None, f"{c.id}: {ref} doesn't exist"


def test_known_gaps_from_the_rule_break_tests_are_written_down():
    gaps = {c.id: c.gap for c in load_chunks() if c.gap}
    assert "TABLE_LEVEL" in gaps["hard.sensitive_data"]
    assert "spelling variant" in gaps["hard.money_double_approval"]


# ---------- search ----------

def test_short_delivery_finds_the_ticket_method(index):
    hits = top(index, "The fish delivery came 4 cases short, what do I do?")
    assert all(h.startswith("method.tickets") for h in hits)


def test_design_questions_find_the_design(index):
    assert top(index, "Where do I find the Stew recipe costing?")[0] == "design.inventory_stew"
    assert top(index, "what does the half circle Mesh option do")[0] == "design.hiwr_half_circle"


def test_ai_decision_disputes_find_the_hearing_rules(index):
    assert "policy.hearings" in top(index, "someone disagrees with what the AI decided")


def test_chunks_above_the_level_are_not_returned():
    chunks = load_chunks()
    secret = next(c for c in chunks if c.id == "method.staffing_payroll.2")
    secret.min_level = 3
    idx = RuleIndex(chunks, HashEmbedder(), cache_dir=None).build()
    assert secret.id not in [c.id for _, c in idx.search("payroll next week", level=0, k=20)]
    assert secret.id in [c.id for _, c in idx.search("payroll next week", level=3, k=20)]


def test_hard_rules_are_always_in_the_context_even_for_unrelated_questions(index):
    block, hits = index.context("what's the weather like", level=0)
    for c in index.hard:
        assert c.text in block
    assert "enforced in code" in block


def test_designed_features_are_labeled_so_the_model_cant_claim_they_work(index):
    block, _ = index.context("tap the logo to switch business", level=0)
    assert "[design.logo_switch_business (designed)]" in block


# ---------- embedders ----------

def test_ollama_embedder_request_shape():
    sent = []

    def post(url, payload):
        sent.append((url, payload))
        return {"embeddings": [[1.0, 0.0]] * len(payload["input"])}

    e = OllamaEmbedder("http://localhost:11434/", "nomic-embed-text", post=post)
    e.embed(["a", "b"], "document")
    e.embed(["q"], "query")
    assert sent[0][0] == "http://localhost:11434/api/embed"
    assert sent[0][1] == {"model": "nomic-embed-text", "input": ["search_document: a", "search_document: b"]}
    assert sent[1][1]["input"] == ["search_query: q"]


def test_ollama_down_falls_back_to_the_word_matcher():
    def down(url, payload):
        raise EmbedderUnavailable("Ollama isn't answering")

    idx = RuleIndex(embedder=OllamaEmbedder(post=down), cache_dir=None).build()
    assert idx.embedder.name == "hash" and "word matcher" in idx.note
    assert idx.search("Stew recipe costing", 2, 1)[0][1].id == "design.inventory_stew"


def test_vectors_are_cached_and_only_changed_chunks_are_re_embedded(tmp_path):
    calls = []

    def post(url, payload):
        calls.append(len(payload["input"]))
        return {"embeddings": [HashEmbedder()._vec(t) for t in payload["input"]]}

    chunks = load_chunks()
    RuleIndex(chunks, OllamaEmbedder(post=post), cache_dir=tmp_path).build()
    assert calls == [len(chunks)]
    chunks[0].text += " Changed."
    idx = RuleIndex(chunks, OllamaEmbedder(post=post), cache_dir=tmp_path).build()
    assert calls[1] == 1 and idx.embedded_now == 1
    cached = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert len(cached) == len(chunks)


def test_config_rejects_an_unknown_embedder():
    with pytest.raises(ValueError):
        config_mod.load("/nonexistent", {"rules": {"embedder": "cloud"}})


def test_embeddings_never_go_to_the_cloud():
    """Questions can hold real names, so the only embedders are local ones."""
    cfg = config_mod.load("/nonexistent", {"mode": "cloud"})
    assert make_index(cfg).embedder.name == "ollama"
    assert "localhost" in make_index(cfg).embedder.host


# ---------- the runner ----------

def test_runner_sends_hard_rules_and_matching_chunks_not_whole_files(sample):  # noqa: F811
    agent, be = make(sample, "chef_marco", mode="local")
    agent.rules = RuleIndex(embedder=HashEmbedder(), cache_dir=None).build()
    agent.ask("The fish delivery came 4 cases short")
    system = be["local"].seen[0]
    assert "Hard rules (always on" in system
    assert "method.tickets" in system
    assert "# Methods loaded for this request" not in system
    assert agent.last_route["rules"]


def test_retrieval_off_keeps_the_old_whole_method_files(sample):  # noqa: F811
    agent, be = make(sample, "chef_marco", mode="local", rules={"retrieval": False})
    assert agent.rules is None
    agent.ask("The fish delivery came 4 cases short")
    assert "# Method: ticket system" in be["local"].seen[0]


class EmptyIndex(RuleIndex):
    """The worst case: the search finds nothing and even the hard-rule reminders are gone."""

    def context(self, query, level=0, k=6, boost_tags=()):
        return "", []


@pytest.mark.parametrize("member,steps,check", [
    ("fry_rosa", [("query_db", {"sql": "SELECT * FROM payroll_lines"})], lambda r: "refused" in json.dumps(r).lower()),
    ("chef_marco", [("propose_money_action", {"kind": "refund", "description": "Refund 2 tacos", "amount": 18.0})],
     lambda r: PENDING in json.dumps(r)),
])
def test_hard_rules_hold_with_no_rules_in_the_prompt(sample, member, steps, check):  # noqa: F811
    agent, be = make(sample, member, local=steps, mode="local")
    agent.rules = EmptyIndex(embedder=HashEmbedder(), cache_dir=None)
    reply = agent.ask("anything")
    assert "Hard rules" not in be["local"].seen[0]
    assert check(be["local"].results[0])
    if steps[0][0] == "propose_money_action":
        assert PENDING in reply, "the approval guard adds it to the reply even if the model forgets"
