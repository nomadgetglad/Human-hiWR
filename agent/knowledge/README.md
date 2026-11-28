# Rules and app design, as searchable chunks

The assistant used to get whole method files pasted into its prompt, picked by keywords. Now every rule and
every part of the app design is a small chunk, turned into a vector (a list of numbers that stands for its
meaning), and each request gets only the chunks closest to the question.

What goes in the prompt for each request:

1. `core.md`, the voice and the always-on instructions (unchanged).
2. Every chunk in `hard_rules.md`, always, whatever the question.
3. The `top_k` chunks (default 6) closest to the question, from `design.md`, `governance.md` and the method files
   in `../prompts/methods/`, each split into one chunk per paragraph or list item. Chunks for the method the
   keyword router picked get a small boost.

## Retrieval is context, not enforcement

The search decides what the model reads, nothing more. Double approval on money, access levels, the W-2 and DM
limits, privacy, append-only history and "a person confirms" are enforced in code: `agent/tools.py`,
`agent/runner.py`, `agent/privacy.py` and `hiwr/`. Each hard rule lists that code under `enforced_by`, and
`tests/test_rules_vectors.py` checks the code exists. The same tests empty the prompt of every rule and check
that staff still can't read payroll and that money still comes back pending.

So a chunk that's missing, wrong or never found can make an answer worse, but it can't open a hole. If you add a
rule that must always hold, write the code and its test first, then the chunk.

`gap:` on a hard rule lists the known holes from the rule-break tests (PR 17) until they're fixed. Gaps are
kept out of the prompt.

## Chunk format

```
## design.inventory_stew
kind: design            # hard, method, design or policy
tags: inventory, stew, recipe
status: built           # design and policy only: built, designed, planned or draft
min_level: 0            # optional; the lowest access level that gets this chunk
enforced_by: hiwr_contacts.approve   # hard rules only

One small idea, in plain words.
```

`status` goes into the prompt next to the chunk, so the assistant never tells anyone a designed or planned
screen already works.

## Embeddings stay on this computer

Questions can contain real names, so embeddings are never made in the cloud, even in cloud mode.

- `embedder = "ollama"` (default): run `ollama pull nomic-embed-text` once. Vectors are cached in
  `agent/.vector_cache/` and only changed chunks are re-embedded.
- `embedder = "hash"`: a built-in word matcher, no install. It matches words, not meaning, and it takes over
  automatically when Ollama isn't answering.

```
python -m agent.vectors list                          # every chunk, kind, status, tags
python -m agent.vectors build                         # embed (or refresh) the index
python -m agent.vectors search "fish came short" --level 2
```

Set `retrieval = false` under `[rules]` in `config.toml` to go back to whole method files.
