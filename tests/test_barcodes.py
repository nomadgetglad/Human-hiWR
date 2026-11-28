"""Barcodes: check digits, pack levels, GS1-128 lots, 2-prefix deli codes, unknown codes and lot holds."""
import pytest

from hiwr_barcodes import (add_barcode, check_digit, confirm_barcode, decode_variable, expand_upce, house_code,
                           is_valid, parse_gs1, receive, scan, set_lot_status, to_gtin14, trace_lot, with_indicator)

D = "2026-11-28"


@pytest.mark.parametrize("body,digit", [("03600029145", 2), ("400638133393", 1), ("1003600029145", 9),
                                        ("40000001023", 6), ("00380000000000123", 9)])
def test_check_digits(body, digit):
    assert check_digit(body) == digit


def test_every_single_digit_typo_is_caught():
    good = "036000291452"
    for i in range(12):
        for d in "0123456789":
            if d != good[i]:
                assert not is_valid(good[:i] + d + good[i + 1:])


def test_gtin14_one_key_for_upc_and_ean():
    assert to_gtin14("036000291452") == to_gtin14("0036000291452") == "00036000291452"
    assert expand_upce("00123457") == "001234000057"
    assert to_gtin14("00123457") == "00001234000057"
    with pytest.raises(ValueError):
        to_gtin14("036000291453")


def test_pack_level_codes():
    assert with_indicator("036000291452", 1) == "10036000291459"
    assert with_indicator("036000291452", 5) == "50036000291457"
    assert house_code(1023) == "400000010236"


def test_gs1_128_parentheses_and_raw_scan_agree():
    label = "(01)10036000291459(17)270315(10)LOT4471B(3103)004536"
    raw = "]C10110036000291459172703151" + "0LOT4471B\x1d3103004536"
    a, b = parse_gs1(label), parse_gs1(raw)
    assert a == b == {"gtin": "10036000291459", "expires_on": "2027-03-15", "lot": "LOT4471B", "net_kg": 4.536}
    assert parse_gs1("(01)10036000291459(17)270200")["expires_on"] == "2027-02-28"   # day 00 = end of month
    with pytest.raises(ValueError):
        parse_gs1("(01)10036000291458")


def test_variable_measure_decode():
    assert decode_variable("201234005995") == {"item_code": "01234", "amount": 599}
    assert decode_variable("036000291452") is None


@pytest.fixture
def inv(db):
    db.execute("INSERT INTO products VALUES ('COLA-12','Cola 12oz can','drinks',125,48,6,30,NULL)")
    db.execute("INSERT INTO materials VALUES ('chicken thigh','lb',30,1)")
    db.execute("INSERT INTO materials VALUES ('turkey breast sliced','lb',12,1)")
    add_barcode(db, "036000291452", "product", "COLA-12", "each", 1, "each", "vendor", "owner_alex", D)
    add_barcode(db, "50036000291457", "product", "COLA-12", "case", 24, "each", "vendor", "owner_alex", D)
    add_barcode(db, "10012345000208", "material", "chicken thigh", "case", 40, "lb", "vendor", "owner_alex", D)
    db.execute("INSERT INTO variable_measure_items VALUES ('01234','material','turkey breast sliced','price',899,'lb')")
    return db


def test_case_scan_adds_24_not_1(inv):
    r = scan(inv, "50036000291457", "cook_luis", D + "T07:00")
    assert (r["result"], r["base_qty"], r["pack_level"]) == ("ok", 24, "case")
    receive(inv, r, "cook_luis", D + "T07:00")
    assert inv.execute("SELECT back_stock FROM products WHERE sku='COLA-12'").fetchone()[0] == 54


def test_bad_read_is_logged_and_moves_nothing(inv):
    r = scan(inv, "50036000291458", "cook_luis", D + "T07:00")
    assert r["result"] == "bad_check_digit"
    assert inv.execute("SELECT result FROM scan_log").fetchone()[0] == "bad_check_digit"
    with pytest.raises(ValueError):
        receive(inv, r, "cook_luis", D + "T07:00")


def test_unknown_code_needs_a_different_manager(inv):
    assert scan(inv, "10012345000017", "cook_luis", D)["result"] == "unknown_code"
    add_barcode(inv, "10012345000017", "product", "COLA-12", "case", 12, "each", "staff_scan", "chef_marco", D)
    assert scan(inv, "10012345000017", "cook_luis", D)["result"] == "unconfirmed_code"
    with pytest.raises(PermissionError):
        confirm_barcode(inv, "10012345000017", "lead_rosa", D)        # level 1
    with pytest.raises(PermissionError):
        confirm_barcode(inv, "10012345000017", "chef_marco", D)       # taught it himself
    confirm_barcode(inv, "10012345000017", "manager_jo", D)
    assert scan(inv, "10012345000017", "cook_luis", D)["result"] == "ok"


def test_deli_price_becomes_weight(inv):
    r = scan(inv, "201234005995", "cook_luis", D, purpose="sale")
    assert (r["item_ref"], r["base_qty"], r["price_cents"]) == ("turkey breast sliced", 0.666, 599)


def test_lot_capture_hold_and_recall_trace(inv):
    label = "(01)10012345000208(17)270315(10)LOT4471B(3201)000402"
    r = scan(inv, label, "lead_rosa", D + "T08:00", vendor="Sysco", lot_tracked=("chicken thigh",))
    assert (r["result"], r["base_qty"]) == ("ok", 40.2)
    assert scan(inv, "10012345000208", "lead_rosa", D, lot_tracked=("chicken thigh",))["result"] == "needs_lot"
    with pytest.raises(PermissionError):
        set_lot_status(inv, r["lot_id"], "on_hold", "recall", "cook_luis", D)     # level 0
    set_lot_status(inv, r["lot_id"], "on_hold", "recall", "lead_rosa", D)
    with pytest.raises(PermissionError):
        set_lot_status(inv, r["lot_id"], "ok", "release", "lead_rosa", D)         # release needs a manager
    assert scan(inv, label, "lead_rosa", D + "T09:00")["result"] == "lot_on_hold"
    t = trace_lot(inv, "LOT4471B")
    assert t[0]["status"] == "on_hold" and len(t[0]["scans"]) == 1


@pytest.mark.parametrize("table", ["barcodes", "barcode_confirmations", "lots", "lot_events", "scan_log",
                                   "inventory_proposals", "inventory_proposal_decisions"])
def test_new_tables_are_append_only(inv, table):
    import sqlite3
    r = scan(inv, "(01)10012345000208(17)270315(10)L1", "lead_rosa", D)
    set_lot_status(inv, r["lot_id"], "on_hold", "test", "lead_rosa", D)
    inv.execute("INSERT INTO inventory_proposals(kind, item_ref, detail, made_at) VALUES ('reorder','COLA-12','{}',?)", (D,))
    inv.execute("INSERT INTO inventory_proposal_decisions VALUES (1,'accepted','manager_jo',?)", (D,))
    with pytest.raises(sqlite3.DatabaseError):
        inv.execute(f"DELETE FROM {table}")
