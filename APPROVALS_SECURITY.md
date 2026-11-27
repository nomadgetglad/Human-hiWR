# Authority security: test results and signed approvals

Tested 2026-11-22 against `main` (sample code in `hiwr/`, UI server in `ui/server/`). Every attack below is a
test in `tests/`. Run them with:

```
pip install -r tests/requirements.txt
python -m pytest tests
```

On the code as it was, 19 of the new tests failed. After the fixes in this branch all 111 pass, and all 16 demos
still print exactly their saved output.

## What broke and what was fixed

| # | Attack | Before | Now |
| --- | --- | --- | --- |
| 1 | Anyone calls `add_plan_owner(db, "me", "me")` | Became account holder (level 4) | Only an existing owner adds an owner; the first one is set up once |
| 2 | Change a $30 refund to $5,000 after two people approved it | Worked; status stayed APPROVED | `money_actions` is append-only (trigger); signed approvals also catch it if the trigger is dropped |
| 3 | Third approver rejects after APPROVED, after it was posted to accounting | Status flipped to REJECTED with the money already booked | Decisions are final once APPROVED or REJECTED |
| 4 | Owner certifies a level-0 cook as a refund approver | Allowed (the `certify()` path skipped the Finance-level rule) | Refused: approvers must be Finance level (3) or higher |
| 5 | Demote an approver to Staff | They could still approve | Approval checks the level at the moment of approving |
| 6 | Negative refund (`-5000`) | Accepted | Refused; 0 stays allowed for waivers, which still need two approvers |
| 7 | Same approver confirms two open cards in the UI | Database blocked the second, but the server crashed (500) | Clear refusal (409), count stays 1/2 |
| 8 | Someone else files the "did it work" check-in on your change | Allowed | Only the person who confirmed it |
| 9 | `POST /api/demo/reset` with no sign-in | Wiped all data | Account holder only |
| 10 | Approve an id that doesn't exist; text an unknown role | Crashed (TypeError / 500) | Clean 400/404 |

Held up without changes: requester approving their own action, uncertified approvers, wrong-scope approvers,
non-owners certifying or setting levels, nobody can be set to level 4, task grants never open costs, payroll,
client records or the ticket financial block (checked for six different tasks), one-hop delegation, expiry
capping, revocation cascade, append-only history tables, confirming someone else's card, replaying a confirmed
card, SQL in ids, posting the same money twice, posting before double approval, and the top bar by role
(owner revenue, Finance labor dollars, level-2 percent only, employees tasks only).

## Business agreements (`hiwr_agreements.py`)

Money terms on an agreement (prices, deposits, rent, fees, pay) follow the same rule as money actions: two
different certified approvers at Finance level, and the new `contract` scope (or the owner-only
`contract_approval` task) for supply, lease, service and similar agreements. Attacks tested in
`tests/test_agreements.py`: HiWR confirming, approving or signing its own proposal; the drafter, the person who
changed the form or the confirmer approving its money; the same approver twice (refused in code and by a unique
index); an uncertified or demoted approver; approving before Confirm; deciding after a rejection or approval;
confirming a stale card; signing a different version than the current one; a Lead signing for the business;
negative, fractional or text dollar amounts; editing or deleting agreements, versions, events or links. A
change to any term is a new version with a new hash, so earlier approvals and signatures stop counting.
Existing databases made before this change keep the old `scope` check on `approver_certifications`, so use the
`contract_approval` task there (or rebuild the table) until a migration lands.

## Still open (not fixable without real identity)

- **Sign-in is a header.** The UI trusts `X-Member`. Anyone on the shop Wi-Fi who types `X-Member: owner_alex`
  is the owner. This is the biggest hole and a test keeps it visible (`test_known_gap_sign_in_is_a_header`).
- **Names are trusted.** `request_money_action(..., requested_by="accounting_sue")` can be called by Jo, who
  then approves "Sue's" request. Same root cause: a name is a string anyone can pass.
- **A database edit beats the rules.** Triggers stop accidents, not someone with the `.db` file, who can drop
  a trigger and insert an approval row.

Signed approvals fix the last two. Passkeys fix the first.

## Making approvals for a locked level tamper-proof

"Encrypting" an approval really means two different jobs:

- **Signing** proves *who* approved *exactly what*. Nobody can fake it or change it afterwards. This is what an
  approval needs.
- **Encrypting** hides data. It is for locked-level data (payroll, financial block) at rest, so a stolen desktop
  or backup can't be read.

### Options

| Option | Stops | Doesn't stop | Cost |
| --- | --- | --- | --- |
| A. Server-side HMAC (one secret key on the desktop signs every approval) | Edits to the database file if the key is kept elsewhere | Anyone with the server or its key forging any approval; doesn't prove the person was there | Standard library, an afternoon |
| B. Per-approver signatures, key on the approver's device (**prototyped**: `hiwr/hiwr_signing.py`, Ed25519) | Forged approvals, approving as someone else, filing a request in someone's name, editing the amount after approval, fake approval rows, replaying a signature on another action | A stolen unlocked phone (revoke the key) | `cryptography` package; key registration by the owner |
| C. Passkeys (WebAuthn): option B where the phone's secure chip holds the key, unlocked by face or fingerprint | Everything in B, plus phishing and copied keys; built-in one-time challenge stops replays; doubles as real sign-in | Needs HTTPS on the shop network (local certificate or a domain) | `webauthn` package on the server; browsers support it natively |
| D. Hash-chained log with the head published daily (**prototyped**: `signed_decisions`, `chain_head()`) | Deleting, editing or reordering any entry; a full rewrite once yesterday's head is off the machine | Forging new entries (that's what B/C are for) | Standard library |
| E. Encrypt locked-level data at rest (SQLCipher or per-field keys) and BitLocker on the Windows desktop | Reading payroll or the financial block from a stolen machine or backup | Insiders using the app normally | BitLocker is free on Windows Pro; SQLCipher is a package |

### Recommendation

**C + D + BitLocker.** Passkeys for sign-in and for signing each money decision, the hash chain with its head
emailed to the account holder every night, and BitLocker on the desktop.

Why: C closes the header hole and the name hole with one piece of work, and approvers already carry phones.
The prototype (B) is the same model, so `verified_status()` carries over: money moves only when it says
APPROVED with no problems. D costs almost nothing and catches the database-file attack. BitLocker covers a
stolen machine for free. Leave per-field encryption (E) until payroll or tickets sync to a cloud service.

Order: (1) passkey sign-in replacing `X-Member`, (2) passkey-signed money decisions, with
`post_money_action` refusing anything `verified_status()` doesn't pass, (3) the nightly chain-head email.

### How the prototype works

1. The account holder's public key is pinned outside the database (printed or in a config file).
2. The account holder signs each approver's public key to register it (`register_key`). Lost phone: register
   the same key as `revoked`.
3. The requester signs the request (`record_signed_request`). Each approver's device signs `payload()`: the
   money action's id, scope, amount, description, requester, request time and ticket, plus the signer and the
   decision.
4. `record_signed_decision` checks the signature, runs the normal `approve()` rules, and appends to the hash
   chain, all or nothing.
5. `verified_status()` ignores the plain `approvals` table and works the status out from signatures alone,
   listing every problem it finds (edited amount, fake row, bad key, broken chain, missing published head).

Tests: `tests/test_signing.py` (forging with another person's key, one person as both approvers, requester
signing, request filed in someone else's name, replay onto another action, approve signature reused as reject,
amount edited after dropping the trigger, fake approval row, planted key, revoked key, deleted or edited
chain entry, full rewrite caught by the published head).
