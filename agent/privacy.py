"""Privacy rules enforced in code, not just asked for in the prompt.

scrub()        always runs, local or cloud, on every tool result and every reply:
               full card numbers become "****1234" and last names are cut to an initial.
Pseudonymizer  runs only on what goes to the cloud: people become labels (Employee A, Client 1,
               Applicant 2), phone numbers and emails become [phone 1] / [email 1]. The map stays on
               this computer. Replies are mapped back before anyone sees them, and tool calls the cloud
               model makes are mapped back before they run.
"""
import json
import re
from dataclasses import dataclass

CARD = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
PHONE = re.compile(r"(?<![\w+])(?:\+?1[ .-]?)?(?:\(\d{3}\)|\d{3})[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+\.[\w.-]+")   # starts only at a word edge: stays fast on "++++..."


def _mask_card(m):
    return "****" + re.sub(r"[^0-9]", "", m.group())[-4:]


@dataclass
class Person:
    ref: str          # how the hiWR data refers to them: member id, or the contact/candidate first name
    first: str
    last: str | None
    kind: str         # member, client, applicant, vendor, extra
    label: str = ""


def _split(name):
    parts = [p for p in re.split(r"\s+", (name or "").strip()) if p]
    if not parts:
        return None, None
    return parts[0], (" ".join(parts[1:]) or None)


def _letters(n):
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _tables(db):
    return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


MEMBER_COLUMNS = [("member_levels", "member"), ("task_grants", "member"), ("staff", "member"),
                  ("approver_certifications", "member"), ("plan_owners", "member"), ("staff_availability", "member"),
                  ("approvals", "approver"), ("money_actions", "requested_by"), ("hr_requests", "member")]


def people_from_db(db, extra_names=(), hide_vendors=False):
    """Everyone the hiWR data names. Member ids look like 'chef_marco'; the first name is the last part."""
    tables, people, seen = _tables(db), [], set()
    members = set()
    for t, c in MEMBER_COLUMNS:
        if t in tables:
            try:
                members |= {r[0] for r in db.execute(f"SELECT DISTINCT {c} FROM {t}") if r[0]}
            except Exception:
                pass
    for m in sorted(members):
        first = m.split("_")[-1].capitalize()
        people.append(Person(m, first, None, "member"))
        seen.add(m)
    if "contacts" in tables:
        kinds = ("client", "vendor") if hide_vendors else ("client",)
        for r in db.execute("SELECT display_name, kind FROM contacts WHERE merged_into IS NULL ORDER BY id"):
            if r[1] in kinds:
                f, l = _split(r[0])
                if f:
                    people.append(Person(f, f, l, r[1]))
    if "candidates" in tables:
        for r in db.execute("SELECT name FROM candidates ORDER BY id"):
            f, l = _split(r[0])
            if f:
                people.append(Person(f, f, l, "applicant"))
    for n in extra_names:
        f, l = _split(n)
        if f:
            people.append(Person(f, f, l, "extra"))
    return people


def scrub(text, people):
    """Always on. Cuts last names to an initial and masks full card numbers."""
    if not text:
        return text
    for p in people:
        if p.last:
            text = re.sub(rf"\b{re.escape(p.first)}\s+{re.escape(p.last)}\b", f"{p.first} {p.last[0]}.", text)
            text = re.sub(rf"\b{re.escape(p.last)},\s*{re.escape(p.first)}\b", f"{p.first} {p.last[0]}.", text)
    return CARD.sub(_mask_card, text)


class Pseudonymizer:
    def __init__(self, people):
        counters = {"member": 0, "client": 0, "applicant": 0, "vendor": 0, "extra": 0}
        self.people = people
        self.by_label = {}
        for p in people:
            n = counters[p.kind]
            counters[p.kind] += 1
            p.label = {"member": f"Employee {_letters(n)}", "client": f"Client {n + 1}", "applicant": f"Applicant {n + 1}",
                       "vendor": f"Vendor {n + 1}", "extra": f"Person {n + 1}"}[p.kind]
            self.by_label[p.label] = p
        # Longest strings first, so "Marcus Bell" is replaced before "Marcus".
        subs = []
        for p in people:
            if p.kind == "member":
                subs.append((p.ref, p.label))
            if p.last:
                subs += [(f"{p.first} {p.last}", p.label), (f"{p.first} {p.last[0]}.", p.label), (p.last, p.label)]
            subs.append((p.first, p.label))
        seen, self._subs = set(), []
        for s, label in sorted(subs, key=lambda x: -len(x[0])):
            if s not in seen and len(s) > 1:
                seen.add(s)
                self._subs.append((re.compile(rf"(?<![\w]){re.escape(s)}(?![\w])"), label))
        self._tokens = {}      # "[phone 1]" -> real value
        self._token_of = {}
        self.hidden_count = 0  # how many swaps were made, for the audit log

    def _token(self, kind, value):
        if value not in self._token_of:
            t = f"[{kind} {sum(k.startswith('[' + kind) for k in self._tokens) + 1}]"
            self._tokens[t], self._token_of[value] = value, t
        return self._token_of[value]

    def hide(self, text):
        if not text:
            return text
        text, n1 = CARD.subn(_mask_card, text)
        text, n2 = EMAIL.subn(lambda m: self._token("email", m.group()), text)
        text, n3 = PHONE.subn(lambda m: self._token("phone", m.group()), text)
        self.hidden_count += n1 + n2 + n3
        for rx, label in self._subs:
            text, n = rx.subn(label, text)
            self.hidden_count += n
        return text

    def _labels_rx(self):
        keys = sorted(list(self.by_label) + list(self._tokens), key=len, reverse=True)
        return re.compile("|".join(re.escape(k) + (r"(?![\w])" if not k.endswith("]") else "") for k in keys)) if keys else None

    def show(self, text):
        """For people reading the reply: labels go back to first names only (never last names)."""
        rx = self._labels_rx()
        if not text or rx is None:
            return text
        return rx.sub(lambda m: self.by_label[m.group()].first if m.group() in self.by_label else self._tokens[m.group()], text)

    def restore_args(self, obj):
        """For tool calls the cloud model makes: labels go back to the reference the hiWR data uses."""
        rx = self._labels_rx()
        if rx is None:
            return obj
        s = json.dumps(obj)
        s = rx.sub(lambda m: (self.by_label[m.group()].ref if m.group() in self.by_label else self._tokens[m.group()]), s)
        return json.loads(s)

    def hide_obj(self, obj):
        return json.loads(self.hide(json.dumps(obj)))
