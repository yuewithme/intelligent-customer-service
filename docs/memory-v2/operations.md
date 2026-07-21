# Memory 2.0 operations runbook

Last updated: 2026-07-21

## Safe defaults

The committed defaults keep writes, shadow reads, and canary injection disabled:

```text
MEMORY_V2_WRITE_ENABLED=false
MEMORY_V2_SHADOW_ENABLED=false
MEMORY_V2_CANARY_ENABLED=false
MEMORY_V2_CANARY_PERCENT=0
```

Qdrant `customer_memory` is a derived index. SQL remains authoritative during
normal operation, incidents, rebuilds, and deletion.

## Rollout sequence

1. Enable `MEMORY_V2_WRITE_ENABLED=true`; keep both read flags off. Verify jobs
   are completing and dead-letter volume is zero.
2. Enable `MEMORY_V2_SHADOW_ENABLED=true`. Shadow rows contain only query hashes,
   counts, latency, and violation flags. They do not alter reply context.
3. Review at least `MEMORY_V2_SHADOW_MIN_SAMPLES` labeled cases. Record the gate
   through `POST /api/v1/memory/subjects/rollout-gates` only after Recall@5,
   temporal accuracy, grounding, scope, and verified-business gates pass.
4. Set a small positive `MEMORY_V2_CANARY_PERCENT`, then enable
   `MEMORY_V2_CANARY_ENABLED=true`. Subject assignment is deterministic.
5. Increase the percentage only after reviewing operations status and sampled
   replies. Each tenant needs its own current passing gate.

No gate is created automatically from telemetry. A reviewer must explicitly
submit the evaluated metrics.

## Monitoring

Use `GET /api/v1/memory/subjects/operations/status?tenant_id=...` to inspect:

- durable job counts by state, especially `retry` and `dead`;
- shadow/canary run counts;
- tenant-scope and verified-business violation totals;
- the latest rollout-gate status and sample count;
- pending, approved, and rejected procedural candidates.

Alert immediately on any scope violation or verified-business violation. Pause
canary growth when retrieval errors increase, dead jobs accumulate, or latency
regresses beyond the service objective.

## Rollback

Set `MEMORY_V2_CANARY_ENABLED=false` or `MEMORY_V2_CANARY_PERCENT=0` to remove
Memory 2.0 from reply context immediately. Shadow collection may continue while
diagnosing. Set `MEMORY_V2_SHADOW_ENABLED=false` to stop retrieval entirely.

Disabling reads does not delete SQL memory. Disable
`MEMORY_V2_WRITE_ENABLED` separately if ingestion or extraction must stop.
Never delete SQL to repair Qdrant.

## Index rebuild

Use `POST /api/v1/memory/subjects/{subject_id}/rebuild-index` with the tenant ID.
The operation deletes that subject's vector points and recreates active episode
points from SQL. Reads always revalidate returned IDs against SQL.

## Correction and purge

- Correct a current fact through
  `POST /api/v1/memory/subjects/{subject_id}/facts/{fact_id}/corrections`.
  The call requires a stable request ID and creates a manual-correction event,
  evidence link, new fact version, and feedback record.
- Manual correction cannot assert payment or other verified commerce status.
- Purge through `POST /api/v1/memory/subjects/{subject_id}/purge`. The body must
  repeat the exact subject ID as confirmation and include a stable request ID.
- A successful purge deletes subject-scoped SQL, legacy profile projections,
  vector points, and caches, while retaining only a content-free audit record.
  A vector failure aborts SQL deletion and records a failed audit for retry.

## Procedural candidates

Submit a candidate only from at least two normalized feedback records. Candidate
text must contain no customer identifier or raw message. Approval records review
state only; the runtime prompt, policies, extractors, and tools never consume an
approved candidate automatically. Any production change requires a separate code
or configuration review with its own tests and deployment.

## Verification commands

From `wechat_rag_bot`:

```powershell
pytest -q tests/test_memory_evaluation.py tests/test_memory_v2_storage.py tests/test_memory_v2_write_pipeline.py tests/test_memory_v2_retrieval.py tests/test_memory_v2_rollout.py tests/test_memory_v2_lifecycle.py tests/test_memory_v2_procedure.py
python -m evaluation.memory.run_memory_eval --dataset evaluation/memory/datasets/memory_v2_seed.jsonl --baseline oracle
```

Before every release, also run `git diff --check`, review the staged diff, and
scan staged content for credentials and customer data.
