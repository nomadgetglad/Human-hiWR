"""Billing settings, read from environment variables (or a billing/.env file that is never committed).

Test mode only until the account holder says otherwise: a live key (sk_live_ / rk_live_) is refused
unless BILLING_ALLOW_LIVE=yes is set on purpose.
"""
import os
import random
import string
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Stripe API version this code is written and tested against (stripe-python 17.1.0)
API_VERSION = "2026-11-18.endive"

# Tags Checkout Sessions so flows can be compared in the Dashboard (label + 8 random letters, fixed once)
CHECKOUT_TAG = "human_hiwr_subscribe_qhtmzpxa"


def load_dotenv(path=HERE / ".env"):
    """Tiny .env reader (KEY=VALUE lines). Values already in the environment win."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _int(env, name, default=None):
    v = env.get(name)
    return int(v) if v not in (None, "") else default


@dataclass
class Settings:
    secret_key: str
    webhook_secret: str | None
    public_url: str
    admins: dict                      # token -> name, for the people who run billing at Human Software
    db_path: str
    allow_live: bool = False
    # Product tax codes (txcd_...). Confirm these with your tax advisor; see billing/README.md.
    tax_code_monthly: str | None = None
    tax_code_setup: str | None = None
    tax_behavior: str = "exclusive"   # "exclusive": tax added on top of the price; "inclusive": price includes tax
    # Stripe Tax on every Checkout, subscription and invoice. Off only to try the rest before the Tax
    # settings (head office address) exist; Stripe collects no tax at all while it is off.
    automatic_tax: bool = True
    prices: dict = field(default_factory=dict)

    @property
    def live(self):
        return "_live_" in self.secret_key


def settings(env=None):
    if env is None:
        load_dotenv()
        env = os.environ
    key = env.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY is not set. Put a TEST key in billing/.env (see .env.example).")
    allow_live = env.get("BILLING_ALLOW_LIVE", "").lower() == "yes"
    if "_live_" in key and not allow_live:
        raise RuntimeError("Refusing a LIVE Stripe key. Billing runs in test mode until BILLING_ALLOW_LIVE=yes is set.")
    if "_test_" not in key and "_live_" not in key:
        raise RuntimeError("STRIPE_SECRET_KEY doesn't look like a Stripe key")
    admins = {}
    for part in env.get("BILLING_ADMINS", "").split(","):
        name, _, token = part.strip().partition(":")
        if name and token:
            if len(token) < 24:
                raise RuntimeError(f"Admin token for {name} is too short (24+ characters)")
            admins[token] = name
    behavior = env.get("BILLING_TAX_BEHAVIOR", "exclusive")
    if behavior not in ("exclusive", "inclusive"):
        raise RuntimeError("BILLING_TAX_BEHAVIOR is exclusive or inclusive")
    return Settings(
        secret_key=key,
        webhook_secret=env.get("STRIPE_WEBHOOK_SECRET") or None,
        public_url=env.get("BILLING_PUBLIC_URL", "http://localhost:8100").rstrip("/"),
        admins=admins,
        db_path=env.get("BILLING_DB", str(HERE / "billing.db")),
        allow_live=allow_live,
        tax_code_monthly=env.get("BILLING_TAX_CODE_MONTHLY") or None,
        tax_code_setup=env.get("BILLING_TAX_CODE_SETUP") or None,
        tax_behavior=behavior,
        automatic_tax=env.get("BILLING_AUTOMATIC_TAX", "on").lower() != "off",
        prices={
            "monthly": _int(env, "BILLING_PRICE_MONTHLY_CENTS"),
            "module": _int(env, "BILLING_PRICE_MODULE_CENTS"),
            "location": _int(env, "BILLING_PRICE_LOCATION_CENTS"),
            "setup": _int(env, "BILLING_PRICE_SETUP_CENTS"),
        },
    )


def new_token():
    """A random admin token for BILLING_ADMINS (python -m billing.config)."""
    return "".join(random.SystemRandom().choice(string.ascii_letters + string.digits) for _ in range(40))


if __name__ == "__main__":
    print(new_token())
