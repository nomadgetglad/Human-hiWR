# Human-hiWR

Claude Code reads this file at the start of every session in this folder. Keep it short and current.

HiWR is the data layer (records, rules, approvals). Human is the app people use on top of it. The two are built
in sync: HiWR supplies the quantities, Human supplies the options and the quantities each option needs.
The repo is **public**.

## Layout

| Path | What lives there |
| --- | --- |
| `hiwr/` | Core modules (`hiwr_*.py`), `schema.sql`, demos (`*_demo.py`) and their saved outputs (`*_output.txt`). Python 3 + SQLite, standard library only (`hiwr_signing.py` and `hiwr_vault.py` need `cryptography`). |
| `ui/server/` | FastAPI app (`app.py`) over the `hiwr/` modules. Filters every response by the member's access level. |
| `ui/web/` | React 19 + React Aria PWA, TypeScript, built with Vite into `ui/web/dist/` (git-ignored). |
| `agent/` | (PR 6) Operations assistant runner (Ollama locally, Claude in the cloud). Prompts in `agent/prompts/`. See `agent/README.md`. |
| `billing/` | (PR 11) Stripe billing for Human Software's own customers. Test mode only. See `billing/README.md`. |
| `tests/` | pytest suite for everything above. `conftest.py` forces `HUMAN_DB=:memory:`. |
| `APPROVALS_SECURITY.md` | Attacks tested, fixes, and the known open gaps. Read before touching auth or approvals. |

New code goes next to its kind: a new business module is `hiwr/hiwr_<name>.py` with a `<name>_demo.py` and saved
output; new tables go in `schema.sql` (or `agent/agent_schema.sql`); new screens go in `ui/web/src/`; every
feature gets a test file in `tests/`.

## Commands (run from the repo root unless noted)

```
pip install -r tests/requirements.txt          # test deps; `ui/requirements.txt` adds uvicorn
python -m pytest tests                         # full suite
python -m pytest tests/test_money_approval.py  # one file while iterating
cd hiwr && python run_all_demos.py             # demos must match their saved *_output.txt
cd hiwr && python run_all_demos.py --update    # only when you changed a demo's output on purpose
cd ui/web && npm install && npm run build      # type check (tsc) + build the PWA
python -m uvicorn server.app:app --app-dir ui --host 127.0.0.1 --port 8000   # run the app
python -m agent --sample --as owner_alex       # try the assistant on sample data
```

On Windows use `py` for `python`. Live UI editing: `npm run dev` in `ui/web/` (port 5173) with the server running.

Environment switches: `HUMAN_DATA=sample` (sample restaurant, or a demo name), `HUMAN_DB` (database path),
`HUMAN_KEY` (vault key instead of `human.key`), `HUMAN_AUTH=off|force`, `HUMAN_GUEST`, `HUMAN_AGENT_CONFIG`,
`HUMAN_NUM_GPU=0` (assistant on CPU). Agent mode (`local`, `cloud`, `hybrid`) is set in `agent/config.toml`,
copied from `agent/config.example.toml`. The API key comes from `ANTHROPIC_API_KEY`, never a file in the repo.

## Coding style

- Python: plain functions over a `sqlite3` connection, type hints where they help, no ORM. Keep `hiwr/` free of
  third-party packages unless there is no standard-library way.
- SQL: always parameterized (`?` placeholders). Never build a query with f-strings or `+` from any input.
- TypeScript: strict, React function components, React Aria for anything interactive. 60px touch targets,
  WCAG 2.2 AA contrast, colour is never the only signal. Screen labels in English and Spanish (`i18n.ts`).
- User-facing text is plain and short. Sample people, prices and fees are marked as sample data.

## Rules that must never be broken

- **Secrets stay out of git.** Never commit `ui/human.key`, `*.key`, `*.db`, `billing/.env`, `agent/config.toml`,
  API keys, Stripe keys, setup codes or sandbox claim links. Check `git status` before every commit.
- **`HUMAN_AUTH=off` only on 127.0.0.1.** Never pair it with `--host 0.0.0.0`, a tunnel, Tailscale Funnel or
  a shared test copy. Sample mode also skips sign-in unless `HUMAN_AUTH=force`, so a shared copy needs `force`.
- **Money never moves on its own.** Refunds, fees, payroll, chargebacks and paid re-ships are proposals marked
  `PENDING - requires double approval`, signed off by two different certified approvers at Finance level (3)
  or higher, never the requester. Do not add, weaken or bypass any path around this, including in tests, seed
  data, the agent's tools or admin shortcuts. No tool or endpoint may approve, pay, send messages or write to
  QuickBooks.
- **HiWR proposes, a person confirms.** Every change goes through the confirm card (Confirm / Fix / Cancel), is
  checked again on Confirm, and is followed by the authority check-in (did it work, did it do what was meant).
- **History is append-only.** New tables get append-only triggers; corrections add a new row or version.
  Never `UPDATE` or `DELETE` history, ticket events or money actions.
- **Access levels** 0 (Staff) to 4 (Account holder) are set only by the account holder. Task grants never unlock
  costs, payroll, client records or the ticket financial block. Filter on the server, never only in the UI.
- **Privacy:** no last names and at most the last 4 digits of a card or ID number in code, output, tickets or
  replies. The agent's cloud calls go through `agent/privacy.py`.
- **Known gaps, don't make them worse:** there is no tenant isolation yet (one database per business, nothing
  stops cross-business reads if two share a server), and the sample switcher trusts the `X-Member` header.
  Flag new code that depends on either.

## How to work

1. **Plan before editing.** Read the module, its demo and its tests first. For anything beyond a one-line fix,
   write a short plan (files to touch, what changes, how it will be tested) and confirm it before editing.
2. **Small tasks.** One feature or fix per branch and pull request. Don't mix refactors with behaviour changes.
3. **Test as you go.** Add or update tests with the change. Re-run only the failing tests while fixing, then the
   full suite and the demos once before the PR. Run `npm run build` if `ui/web/` changed.
4. **Touching auth, approvals, access or money?** Add an attack test next to the ones in
   `tests/test_money_approval.py` and `tests/test_access.py`, and update `APPROVALS_SECURITY.md`.
5. **Pull requests** describe what changed, what was tested, and any rule above the change comes close to.
   Never merge your own PR.
