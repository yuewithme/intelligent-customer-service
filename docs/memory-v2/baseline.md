# Memory 2.0 WP0 baseline

Captured: 2026-07-21

Dataset contract: `memory_eval.v1`

Production behavior changed: no

## 1. Existing storage coverage

The following aggregate-only snapshot was taken from the local development
database. No message content or direct identifier was copied into this document.

| Measure | Count |
|---|---:|
| User profile rows | 894 |
| Eyun contact rows | 879 |
| Profiles with basic contact information | 879 |
| Conversation memory rows | 183 |
| Customer-role memory rows | 113 |
| Assistant-role memory rows | 69 |
| Human-role memory rows | 1 |
| Profiles with no conversation memory | 880 |
| Profiles with at least one customer message | 14 |
| Profiles with at least three customer messages | 8 |
| Profiles with customer tags | 0 |
| Profiles with product interests | 3 |
| Profiles with pain points | 3 |
| Profiles with active opportunity data | 10 |
| Profiles with a non-unknown sales stage | 12 |

This confirms that the current profile table is primarily a contact projection,
not a broadly populated long-term conversational memory.

## 2. Existing behavior represented by the baseline

The `recent_only` baseline intentionally approximates the current retrieval
shape: take the most recent five events and perform no versioned memory
operation. It is deterministic and does not call an LLM, database, or vector
service. It is a regression comparison point, not a claim that it reproduces
every production code path.

Command:

```powershell
cd wechat_rag_bot
python -m evaluation.memory.run_memory_eval --baseline recent_only
```

Committed result: `evaluation/memory/baselines/recent_only.json`

Key results on the 18-case synthetic seed:

| Metric | Result |
|---|---:|
| Operation precision | 0.0000 |
| Operation recall | 0.0000 |
| Operation F1 | 0.0000 |
| Retrieval Recall@5 | 0.8750 |
| Temporal operation accuracy | 0.0000 |
| Abstention accuracy | 0.8889 |
| Deletion residue count | 4 |

The oracle command must remain perfect and is used to catch evaluator or dataset
contract regressions:

```powershell
python -m evaluation.memory.run_memory_eval --baseline oracle
```

## 3. Interpretation and next gate

WP1 must not attempt to improve these metrics by changing the evaluator. It must
produce idempotent tenant-scoped events and identities while production reads
remain unchanged. Memory extraction, temporal consolidation, retrieval, and
purge improvements belong to their later work packages.

The checked-in dataset is a synthetic contract seed. Before WP4 can enable a
production read canary, it must be supplemented by 100-200 reviewed, redacted
internal cases stored through the same schema. Raw customer conversations and
the re-identification mapping must remain outside source control.
