# Operations assistant runner

Runs the Human hiWR operations assistant (the v8 prompt) against the hiWR data, on Dad's Windows desktop.

The rules that matter most are enforced in code here, so they hold no matter which model answers or what
anyone types:

| Rule | Where it's enforced |
| --- | --- |
| Access levels (0 Staff to 4 Account holder) | `tools.py`: tools above your level aren't offered; database reads are checked table by table and column by column; files are checked by name pattern |
| Financial block only at Finance level | `get_ticket`, `list_tickets`, `query_db` (the column reads as null), `open_ticket` drops it below Finance |
| No last names, at most the last 4 of a card | `privacy.scrub()` runs on every tool result, every draft and every reply, local or cloud |
| Nothing is approved, paid, sent or posted | Those tools don't exist. `propose_money_action` files the item as PENDING with its approvers, and the reply is checked to make sure it says so |
| A ticket closes only when its money is settled | `close_ticket()` from `hiwr_processes.py` |
| Sale verification (order # + last 4) | `verify_sale`; says only "could not verify", locks after 3 misses |
| QuickBooks read only, and only if connected | `quickbooks_read` is the only QuickBooks tool and reads an export file |
| History never overwritten | New tables are append-only (triggers), tickets change through `ticket_events` |
| Cloud sees labels, not people | `privacy.Pseudonymizer`: names become Employee A / Client 1 / Applicant 2, phones and emails become [phone 1] / [email 1]. The map never leaves the computer |

Every turn writes one row to `agent_log` (who asked, local or cloud, which tools ran). Never the content.

## Local, cloud or hybrid

One setting, `mode` in `agent/config.toml`:

- **local**: everything runs through Ollama on the desktop. Nothing leaves the machine.
- **cloud**: every request goes to Claude (`claude-opus-5-5`) after names are swapped for labels.
- **hybrid** (default): routine lookups run locally. Requests that need the weekly plan or pattern analysis
  go to Claude. If Claude can't be reached, the request runs locally and the reply says so.

Start any message with `/local` or `/cloud` to choose for that one message. Add `--show-cloud` to see exactly
what is sent.

## Prompt layout

- `prompts/core.md`: voice, missing data, access, live person, privacy, approvals, output format. Always sent.
- `prompts/methods/*.md`: client service, tickets, staffing and payroll, inventory, accounting and warranties,
  planning, patterns. The router loads the ones a request needs, and the model can load more with
  `load_method`. This keeps the local model's context small.

Changes from the v8 text: restaurant "tickets" are now "table checks" so they don't get mixed up with
support tickets; the priority order is stated once, in the core; QuickBooks is used only "if connected";
and the planning example says "Employee A" instead of a real-sounding name.

## Set up on Windows

1. Install Python 3.11 or newer from python.org (tick "Add python.exe to PATH").
2. Install Ollama from ollama.com, then in a terminal:
   ```
   ollama pull gpt-oss:20b
   ollama pull nomic-embed-text
   ```
   `qwen3:30b` also works. Both need a recent graphics card or plenty of RAM (32 GB or more is comfortable).
   The runner sets `num_ctx` to 16384, because Ollama's default context cuts the prompt off.
3. Get the code: `git clone https://github.com/nomadgetglad/Human-hiWR` and `cd Human-hiWR`.
4. For cloud or hybrid: `pip install anthropic`, then set the key for your user:
   ```
   setx ANTHROPIC_API_KEY "sk-ant-..."
   ```
   Close and reopen the terminal afterwards. The key is never stored in the repo.
5. Copy `agent\config.example.toml` to `agent\config.toml` and set `mode`, the database path and the business
   phone, text number and hours (left empty, the assistant says they're missing instead of inventing them).

## Run it

```
python -m agent --sample --as owner_alex          # try it on the sample restaurant
python -m agent --as chef_marco                   # chat as the chef, on your own data
python -m agent --as fry_rosa "Can I swap Saturday?"
python -m agent --as owner_alex --mode local      # force local for this run
```

`--as` is who is asking, and it sets the access level from the hiWR data. An unknown name gets level 0.
For now whoever runs the command picks the name, the same gap as the UI's sign-in (see
`../APPROVALS_SECURITY.md`). On a shared desktop, give each person their own Windows account and a shortcut
with their name, until passkey sign-in lands.

Business files (invoices, schedules, fee policy, warranty terms) go in the data folder (`data/` by default) as
CSV or text. Drafts the assistant writes land in `data/drafts/`. Nothing there is ever sent.

## Rules and app design

The rules and the app design are small chunks in a local vector index. Each request gets the hard rules
(always) plus the few chunks closest to the question. The hard rules are enforced in code either way; the
search only shapes what the model reads. See `knowledge/README.md`.

## Tests

From the repo root: `python -m pytest tests/test_agent.py`. A scripted fake model stands in for Ollama and
Claude, so the tests run offline and cost nothing.
