"""Picks which method files a request needs and, in hybrid mode, where it runs.

Routine lookups (a ticket, a shift swap, a stock count) stay on the local model. Requests that need the
heavy methods (weekly plan, pattern analysis by default) go to the cloud. Start a message with /local or
/cloud to choose yourself.
"""
import re

KEYWORDS = {
    "planning": r"next week|coming week|upcoming|this week|forecast|enough (people|staff)|payroll (look|be)|can we afford|"
                r"weekly (plan|outlook)|outlook|labor (need|required)|plan (the|for)",
    "patterns": r"pattern|trend|root cause|keeps? (going wrong|happening)|recurring|why do we keep|again and again|"
                r"correlat",
    "tickets": r"ticket|dispute|short(ed)? (ship|deliver)|shipment|damaged|delivery|deliveries|refund|complain|"
               r"chargeback|return|wrong item|late truck",
    "client_service": r"\border\b|appointment|no.?show|reservation|\btable|missed|late cancel|\bclient|customer|guest|"
                      r"walk.?in|wait ?list|upsell",
    "staffing_payroll": r"shift|schedule|cover(age)?|time off|sick|overtime|payroll|hours|clock.?in|swap|\bpay\b|reward|"
                        r"recogni",
    "inventory": r"inventory|stock|\bpar\b|86|reorder|invoice|\bupc|\bsku|recipe|spoilage|order more|purchase order|\bpo\b",
    "accounting_warranties": r"accounting|revenue|expense|profit|quickbooks|warrant|contract|anticipated cost|margin",
}
_RX = {k: re.compile(v, re.I) for k, v in KEYWORDS.items()}


def methods_for(text):
    return sorted(k for k, rx in _RX.items() if rx.search(text))


def route(cfg, text):
    """Returns (backend, methods, why, text without any /local or /cloud prefix)."""
    forced = None
    m = re.match(r"\s*/(local|cloud)\b\s*", text)
    if m:
        forced, text = m.group(1), text[m.end():]
    methods = methods_for(text)
    mode = cfg["mode"]
    if forced:
        return forced, methods, f"you asked for {forced}", text
    if mode in ("local", "cloud"):
        return mode, methods, f"mode is {mode}", text
    heavy = sorted(set(methods) & set(cfg["hybrid"]["cloud_methods"]))
    if heavy:
        return "cloud", methods, "needs " + ", ".join(heavy), text
    return "local", methods, "routine request", text
