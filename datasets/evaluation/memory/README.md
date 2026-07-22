# Memory evaluation datasets

`memory_v2_seed.jsonl` is a synthetic, non-identifying contract dataset. It is
checked into source control so every work package is measured against identical
cases.

Run the current architecture proxy:

```powershell
python -m evaluation.memory.run_memory_eval --baseline recent_only
```

Validate the scorer with an oracle:

```powershell
python -m evaluation.memory.run_memory_eval --baseline oracle
```

Score an implementation prediction file:

```powershell
python -m evaluation.memory.run_memory_eval --predictions path\to\predictions.jsonl
```

Prediction rows contain `id`, `operations`, `retrieved_event_ids`,
`must_abstain`, and `remaining_artifact_ids`. Operations use the contract in
`docs/memory-v2/data-contract.md`.

Real conversations may be added only after redaction. Do not commit names,
phone numbers, addresses, email addresses, external platform IDs, tokens, order
numbers, or raw images. Keep an internal mapping outside the repository if a
case must be traceable to its source.
