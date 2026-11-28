"""Inventory vectors: same columns for every item, costs hidden below Manager, reorder rounds to cases,
name similarity only suggests, and a code is never matched by similarity."""
import json
from datetime import date, timedelta

import pytest

from hiwr_barcodes import add_barcode, scan
from hiwr_inventory_vectors import (BASE_FEATURES, COST_FEATURES, column, cosine, decide, hash_embed, matrix,
                                    normalize, possible_duplicates, similar_items, stock_proposals, suggest_for_unknown)

D = "2026-11-28"


@pytest.fixture
def inv(db):
    for sku, name, shelf, back, exp in (("CHIP-01", "Tortilla chips 13oz", 4, 2, None),
                                        ("CHIP-09", "Tortilla Chips 13 oz", 3, 0, None),
                                        ("SOUP-06", "Canned soup 15oz", 40, 200, "2028-06-01"),
                                        ("YOGT-03", "Greek yogurt 32oz", 6, 10, "2026-12-03")):
        db.execute("INSERT INTO products VALUES (?,?,'x',449,210,?,?,?)", (sku, name, shelf, back, exp))
    for back_days in range(1, 29):
        day = (date(2026, 11, 28) - timedelta(days=back_days)).isoformat()
        for sku, n in (("CHIP-01", 2), ("CHIP-09", 1), ("SOUP-06", 1), ("YOGT-03", 1)):
            db.execute("INSERT INTO daily_sales VALUES (?,?,?)", (sku, day, n))
    add_barcode(db, "10012345000017", "product", "CHIP-01", "case", 12, "each", "vendor", "owner_alex", D)
    return db


def test_every_row_has_the_same_columns(inv):
    m = matrix(inv, "chef_marco", D)
    assert m["columns"] == BASE_FEATURES + COST_FEATURES
    assert len(m["rows"]) == 4 and all(len(r) == len(m["columns"]) for r in m["rows"])
    assert all(isinstance(x, float) for r in m["rows"] for x in r)
    assert dict(zip(m["keys"], column(m, "days_of_supply")))["CHIP-01"] == 3.0


def test_staff_never_get_cost_columns(inv):
    m = matrix(inv, "cook_luis", D)
    assert not set(COST_FEATURES) & set(m["columns"])


def test_reorder_rounds_up_to_whole_cases_and_waits_for_a_manager(inv):
    props = {p["item"]: p for p in stock_proposals(inv, "chef_marco", D) if p["kind"] == "reorder"}
    chips = props["CHIP-01"]
    # 2/day x (3 lead + 7 cover) = 20 needed - 6 on hand = 14 -> 2 cases of 12
    assert (chips["cases"], chips["case_size"], chips["units"]) == (2, 12, 24)
    assert "SOUP-06" not in props
    with pytest.raises(PermissionError):
        decide(inv, chips["id"], "accepted", "lead_rosa", D)
    decide(inv, chips["id"], "accepted", "chef_marco", D)
    with pytest.raises(ValueError):
        decide(inv, chips["id"], "rejected", "manager_jo", D)


def test_expiry_risk_flagged(inv):
    risk = [p for p in stock_proposals(inv, "chef_marco", D) if p["kind"] == "expiry_risk"]
    assert [p["item"] for p in risk] == ["YOGT-03"] and risk[0]["expiring_unsold"] == 11


def test_embeddings_are_stable_and_unit_length():
    a = hash_embed("Tortilla chips 13oz")
    assert a == hash_embed("Tortilla chips 13oz")
    assert abs(sum(x * x for x in a) - 1) < 1e-9
    assert normalize("Tortilla Chips 13oz") == normalize("tortilla chips 13 oz")
    assert cosine(hash_embed("tortilla chips"), hash_embed("tortilla chip")) > cosine(hash_embed("tortilla chips"), hash_embed("greek yogurt"))


def test_similar_names_suggest_but_never_link_a_code(inv):
    s = suggest_for_unknown(inv, "10099999000013", "tortilla chips case", D)
    assert s["candidates"][0]["item_ref"] in ("CHIP-01", "CHIP-09")
    assert scan(inv, "10099999000013", "cook_luis", D)["result"] == "unknown_code"
    row = inv.execute("SELECT kind, detail FROM inventory_proposals WHERE id=?", (s["id"],)).fetchone()
    assert row["kind"] == "unknown_code_match" and json.loads(row["detail"])["typed"] == "tortilla chips case"


def test_possible_duplicate_skus(inv):
    pairs = possible_duplicates(inv, D)
    assert [(p["a"], p["b"]) for p in pairs] == [("CHIP-01", "CHIP-09")]


def test_a_custom_embedder_can_be_plugged_in(inv):
    class Fake:
        def embed(self, texts):
            return [[1.0, 0.0] if "soup" in t.lower() else [0.0, 1.0] for t in texts]
    top = similar_items(inv, "soup", embedder=Fake())
    assert top[0]["item_ref"] == "SOUP-06"
