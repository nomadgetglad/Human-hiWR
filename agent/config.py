"""Settings for the agent runner. Defaults live here; agent/config.toml overrides them."""
import copy
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODES = ("local", "cloud", "hybrid")

DEFAULTS = {
    "mode": "hybrid",
    "data": {"db": "hiwr.db", "folder": "data"},
    "local": {"host": "http://localhost:11434", "model": "gpt-oss:20b", "num_ctx": 16384, "timeout_seconds": 300},
    "cloud": {"model": "claude-opus-5-5", "effort": "medium", "max_tokens": 16000},
    "hybrid": {"cloud_methods": ["planning", "patterns"], "fall_back_to_local": True},
    "privacy": {"extra_names": [], "hide_vendors": False},
    "quickbooks": {"connected": False, "export_file": ""},
    "business": {"phone": "", "text_number": "", "hours": "", "on_duty_manager": ""},
    "files": {"levels": {"*payroll*": 3, "*pay_rate*": 3, "*wage*": 3, "*accounting*": 3, "*quickbooks*": 3,
                         "*invoice*": 2, "*contract*": 2, "*vendor_price*": 2}},
}
MIN_NUM_CTX = 16384


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None, overrides=None):
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path) if path else HERE / "config.toml"
    if p.exists():
        with open(p, "rb") as f:
            _merge(cfg, tomllib.load(f))
    if overrides:
        _merge(cfg, overrides)
    if cfg["mode"] not in MODES:
        raise ValueError(f"mode must be one of {MODES}, not {cfg['mode']!r}")
    if cfg["local"]["num_ctx"] < MIN_NUM_CTX:
        raise ValueError(f"local.num_ctx must be at least {MIN_NUM_CTX}; smaller cuts the prompt off")
    return cfg
