# Memory 2.0 architecture contract

Status: implemented through WP3

Contract version: `memory.v1`

Last updated: 2026-07-21

Implementation status: WP3 scoped retrieval and context assembly complete;
production reads remain legacy and Memory 2.0 writes are disabled by default.

## 1. Purpose

Memory 2.0 replaces the current profile-plus-recent-messages behavior with an
evidence-grounded memory subsystem. This document is the implementation boundary
for later work packages; WP0 does not change production reads or writes.

The system must answer four different questions without conflating them:

1. What happened? Immutable source events.
2. What is currently believed to be true? Versioned semantic facts.
3. What past situation is relevant now? Episodic memories linked to source events.
4. What is active in this conversation? Short-lived working state.

`user_profiles` remains a backwards-compatible projection during migration. It is
not an authoritative source in Memory 2.0.

## 2. Non-negotiable invariants

1. SQL is authoritative. Vector indexes and summaries are derived and rebuildable.
2. Every durable fact has `tenant_id`, `subject_id`, source, evidence, valid time,
   status, and version.
3. Every read is scoped by tenant and subject before semantic ranking.
4. Source events are append-only. Correction creates a new event and a new fact
   version; it never rewrites evidence.
5. Assistant messages may support an assistant-commitment episode, but must never
   be the sole evidence for a customer fact.
6. Payment, refund, fulfillment, inventory, and entitlement facts require a
   verified business source.
7. A model output is a candidate operation. Validation and consolidation decide
   whether it becomes durable memory.
8. Deletion covers SQL rows, summaries, vector points, caches, and derived
   projections, and leaves an audit record without deleted content.
9. Production reads remain on the legacy path until shadow evaluation and canary
   gates pass.
10. Procedural memory cannot alter policy or prompts without human approval.

## 3. Target components

```text
chat / human / contact / commerce / tool observations
                         |
                         v
                 immutable event log
                         |
                         v
                  durable memory job
                         |
             deterministic + LLM candidates
                         |
                 evidence/source validator
                         |
                 transactional consolidator
                    /       |        \
                 facts   episodes   working state
                    \       |        /
                     profile projection

query -> scope filters -> exact facts + vector episode candidates
      -> temporal/source rerank -> evidence expansion -> memory context
```

Planned service ownership:

| Component | Responsibility | Planned module |
|---|---|---|
| Identity | Map channel identities to one subject | `app/services/memory_identity_service.py` |
| Event store | Idempotent append and source lookup | `app/services/memory_event_service.py` |
| Job queue | Durable claim, retry, and dead-letter state | `app/services/memory_job_service.py` |
| Extraction | Produce candidate memory operations | `app/services/memory_extraction_service.py` |
| Validation | Enforce evidence and source policy | `app/services/memory_validation_service.py` |
| Consolidation | Apply versioned operations transactionally | `app/services/memory_consolidation_service.py` |
| Projection | Rebuild the compatible user profile | `app/services/memory_projection_service.py` |
| Retrieval | Scope, recall, rerank, and evidence expansion | `app/services/memory_retrieval_service.py` |
| Vector index | Maintain rebuildable episode vectors | `app/services/memory_vector_service.py` |

## 4. Storage boundaries

The target SQL entities are:

- `memory_subjects`: tenant-owned internal customer identity.
- `memory_identities`: unique external identities mapped to a subject.
- `memory_events`: immutable normalized source events with idempotency key.
- `memory_facts`: bitemporal, versioned facts and their evidence.
- `memory_episodes`: situation summaries linked to source events.
- `memory_episode_events`: ordered episode-to-event links.
- `memory_jobs`: durable asynchronous work.
- `memory_feedback`: confirmations and corrections.
- `memory_purge_audits`: content-free deletion audit.

WP1 physically creates `memory_subjects`, `memory_identities`, `memory_events`,
`memory_facts`, and `memory_fact_evidence`. WP2 adds `memory_episodes`,
`memory_episode_events`, and `memory_jobs`. WP3 adds the independently configured
`customer_memory` vector projection, query planner, SQL revalidation, reranking,
and bounded evidence expansion. Feedback and purge tables remain contract targets
for WP5. The worker starts only when `MEMORY_V2_WRITE_ENABLED=true`; no production
adapter enqueues events and no production reply path reads Memory 2.0 yet.

The Qdrant collection defaults to `customer_memory` and is configured separately
from the knowledge-base collection. It stores episode embeddings
and only the minimum filtering payload: tenant, subject, episode ID, status,
time, and embedding version. It must not be queried globally and treated as an
authorization layer.

No similarity graph or graph database is included in the first production
version. Explicit relations such as customer-to-order may be added in SQL after
the flat evidence-grounded baseline is measured.

## 5. Write path

1. Resolve `(tenant, channel, owner, external_user_id)` to `subject_id`.
2. Append a source event using a stable `event_uid`.
3. Enqueue one durable job using a stable deduplication key.
4. Extract deterministic candidates first; use an LLM only for semantic facts
   and episode boundaries.
5. Validate source class, evidence IDs, actor rules, catalog values, time, and
   sensitivity.
6. Consolidate accepted candidates with optimistic version checks.
7. Rebuild the affected projection and asynchronously update vector points.

Chat latency must not depend on steps 3-7. A retry must not duplicate events,
facts, episodes, or vector points.

## 6. Read path

1. Load exact active facts and working state after tenant/subject filtering.
2. Plan which memory classes the current intent needs.
3. Search at most 20 episode candidates in the subject-scoped vector index.
4. Revalidate candidate IDs against SQL status and permissions.
5. Rerank by semantic relevance, intent match, source reliability, temporal
   relevance, and importance.
6. Return at most 8 facts, 2 episodes, and 2-4 source events per episode.
7. Mark evidence gaps as unknown; use a trusted tool or ask the customer instead
   of filling the gap with model inference.

Default retrieval excludes facts supported only by restricted evidence and omits
episodes linked to restricted events. A future privileged caller must opt in
explicitly and still passes tenant/subject SQL revalidation.

The prompt receives a curated `memory_context`, not the entire profile JSON and
not an unbounded transcript.

## 7. Work-package gates

| WP | Deliverable | Exit gate |
|---|---|---|
| WP0 | Contracts, seed data, deterministic evaluator | Dataset validates; tests and baseline command pass |
| WP1 | Identity, SQL models, idempotent events | Duplicate and tenant-isolation tests pass; no read switch |
| WP2 | Durable jobs and versioned writes | Evidence coverage is 100%; retries are idempotent |
| WP3 | Scoped retrieval and context assembly | Scope, temporal, rerank, and evidence contract tests pass; no production read switch |
| WP4 | Dual write, shadow read, canary | No verified-business hallucination or tenant leakage |
| WP5 | Admin correction and purge | SQL/vector/cache deletion residue is zero |
| WP6 | Reviewed procedural candidates | No automatic production policy mutation |

Each work package is a separate change. A later work package must update this
contract and its evaluation cases before changing a stated invariant.
