# Barcodes and inventory vectors: the Python basics

A plain walk-through of `hiwr_barcodes.py` and `hiwr_inventory_vectors.py`, for reading the code and
changing it. Run `python barcode_demo.py` from the `hiwr` folder to see everything below happen.

## The Python pieces used

| Piece | What it looks like | Where it's used |
| --- | --- | --- |
| A string | `"036000291452"` | Barcodes stay strings, never numbers, or the leading 0 would vanish |
| A list | `[6, 30, 36, 1.2]` | One item's stock vector: numbers in a fixed order |
| A list of lists | `[[6, 30, ...], [4, 2, ...]]` | The matrix: one row per SKU, same columns in every row |
| A dict | `{"lot": "LOT4471B", "expires_on": "2027-03-15"}` | What a GS1-128 scan says, by name |
| A function | `def check_digit(body): ...` | One job, a name, inputs in, answer out |
| `for ... in ...` | `for i, c in enumerate(reversed(body))` | Walk the digits from the right, with their position |
| `raise` | `raise PermissionError(...)` | Stop when a rule is broken: a level-0 can't confirm a code |

## The check digit in five lines

```python
def check_digit(body):
    total = sum(int(c) * (3 if i % 2 == 0 else 1) for i, c in enumerate(reversed(body)))
    return (10 - total % 10) % 10
```

`reversed(body)` reads the digits right to left. `i % 2 == 0` is true for every other digit, starting with
the one next to the check digit, which gets weight 3. `total % 10` is the remainder after dividing by 10,
and `10 - that` is what brings the total up to the next multiple of 10. The final `% 10` turns a 10 into 0.
The same function works for UPC-A, EAN-13, GTIN-14 and SSCC because it counts from the right.

## What a "vector" means here

A vector is just a list of numbers where each position always means the same thing. For a SKU:

```
columns: floor  back  on_hand  per_day_7  per_day_28  days_of_supply  days_to_expiry  ...
CHIP-01: 4      14    18       2.0        2.0         9.0             9999.0          ...
```

Because every SKU has the same columns in the same order, the whole store is one table of numbers.
That makes "which items run out before the truck comes?" one pass over the rows instead of a question per
item, and the same table can go straight into PyTorch (`torch.tensor(m["rows"])`) for the hiwr_ml layer.
9999 stands in for "never" (no expiry, no sales) so every cell stays a number.

Cost, price and margin columns are added only for Manager level and up. Staff get the same matrix without them.

## Name embeddings (the other kind of vector)

`hash_embed("Tortilla chips 13oz")` turns a name into 256 numbers, built from its letter triplets
("tor", "ort", "rti" ...). Names that share most triplets get similar numbers. `cosine(a, b)` scores how
close two of these are, from 0 (nothing alike) to 1 (same). It needs no model and gives the same answer
on every computer. The Ollama embedder from `agent/vectors.py` (nomic-embed-text) can be passed in
instead, as long as it has an `embed(list_of_texts)` method.

These scores only **suggest**: "this unknown case looks like CHIP-01", "CHIP-01 and CHIP-09 look like the
same chips". A barcode is always an exact key. It is never linked to an item because a name looked close.

## The rules the code enforces

- A bad check digit is rejected and logged; nothing moves.
- An unknown code asks the person. Their answer is saved as unconfirmed, and a different person at
  Manager level confirms it before it can change stock.
- Lot-tracked items (you choose which) can't be received without a lot. A lot on hold blocks its scans.
  Any Lead can place a hold; releasing, recalling or discarding needs a Manager.
- Reorders, expiry risks and possible duplicates are proposals. A Manager accepts or rejects; accepting a
  reorder still only leads to a purchase draft for a person.
- Every new table is append-only, so the history of codes, lots and scans can't be rewritten.

## Settings that are sample values

`cover_days=7`, `safety_days=2`, `default_lead_days=3` in `stock_proposals`, the similarity cut-offs
(0.3 for suggestions, 0.8 for duplicates), and which items are lot-tracked. Change them when you call the
functions; they aren't fixed in the database.
