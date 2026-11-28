# Human-hiWR

Claude Code reads this file on its own whenever a session starts in this folder. Nothing runs it.

## Layout
- `hiwr/`: sample code, Python 3 + SQLite, standard library only (except `hiwr_signing.py`, which needs `cryptography`,
  and `hiwr_torch.py`, which uses PyTorch when installed and is skipped without it).
- `ui/`: the Human app (FastAPI + React PWA).
- `agent/`: the operations assistant runner (Ollama locally, Claude in the cloud). See `agent/README.md`.
- `tests/`: `python -m pytest tests`. Demos: `cd hiwr && python run_all_demos.py` (checks saved outputs).

## Rules for any change
- History is never overwritten: new tables get append-only triggers.
- Money never moves on its own. Refunds, fees, payroll and purchases are proposals that need two certified
  approvers. Never add a tool or endpoint that approves, pays, sends messages or writes to QuickBooks.
- Access levels 0 to 4 are set only by the account holder. Task grants never unlock costs, payroll or the
  ticket financial block.
- No last names and no full card numbers in code, sample data output, tickets or replies.
- People, prices and fees in the demos are sample data.
- Run the tests and the demos before opening a pull request.
