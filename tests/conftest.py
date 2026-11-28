"""Shared setup. Run from the repo root:  python -m pytest tests"""
import sys
from pathlib import Path

import os

import pytest

os.environ["HUMAN_DB"] = ":memory:"     # tests never touch a real account file
os.environ.setdefault("HUMAN_CHAT_EMBED", "off")   # chat history search by words only, unless a test swaps in an embedder

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hiwr"))
sys.path.insert(0, str(ROOT / "ui"))

from hiwr_contacts import connect, add_plan_owner, certify  # noqa: E402
from hiwr_access import set_level  # noqa: E402

TODAY = "2026-11-22"
OWNER = "owner_alex"


@pytest.fixture
def db():
    """Owner, two Finance-level refund/payroll approvers (jo, sue), a third (kim), a chef at Manager
    level who requests money actions, and a level-0 cook."""
    d = connect()
    add_plan_owner(d, OWNER, "setup")
    for m in ("manager_jo", "accounting_sue", "finance_kim"):
        set_level(d, m, 3, OWNER)
        for scope in ("refund", "payroll"):
            certify(d, m, scope, OWNER)
    set_level(d, "chef_marco", 2, OWNER)
    set_level(d, "lead_rosa", 1, OWNER)
    set_level(d, "cook_luis", 0, OWNER)
    return d
