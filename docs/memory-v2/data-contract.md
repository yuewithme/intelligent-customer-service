# Memory 2.0 data contract

Schema version: `memory.v1`

This contract defines logical fields. WP1 will map them to SQLAlchemy models and
Pydantic schemas without replacing required fields with opaque JSON blobs.

## 1. Common identifiers and time

- `tenant_id`: non-empty tenant boundary.
- `subject_id`: internal stable customer identifier; never an external WeChat ID.
- `event_uid`: globally unique idempotency key derived from the authoritative
  source identifier, not message text alone.
- `occurred_at`: when the source event happened.
- `recorded_at`: when the system persisted a belief.
- `valid_from` / `valid_to`: business-valid interval of a fact.
- All timestamps are timezone-aware ISO 8601 values and are stored in UTC.

## 2. Source event

Required logical fields:

```json
{
  "schema_version": "memory.v1",
  "event_uid": "conversation_message:12345",
  "tenant_id": "tenant_default",
  "subject_id": "subject_01",
  "session_id": "session_01",
  "event_type": "customer_message",
  "actor_type": "customer",
  "content": {"text": "我这次预算三千元"},
  "source_type": "conversation_message",
  "source_id": "12345",
  "trace_id": "trace_01",
  "occurred_at": "2026-07-21T02:00:00Z",
  "sensitivity": "internal"
}
```

Allowed `actor_type` values:

```text
customer, assistant, human_agent, system, business_system
```

Initial `event_type` values:

```text
customer_message, assistant_message, human_message, contact_snapshot,
commerce_event, tool_observation, manual_correction, image_observation
```

## 3. Memory operation

The extractor returns candidates using one of these operations:

| Operation | Meaning |
|---|---|
| `ADD` | A new supported fact or episode |
| `REINFORCE` | Same value, additional independent evidence |
| `SUPERSEDE` | New value replaces an old value from a valid time |
| `DISPUTE` | Sources conflict and neither can safely win |
| `RESOLVE` | Trusted evidence resolves a disputed memory |
| `NOOP` | Nothing durable should be written |

Candidate contract:

```json
{
  "operation": "ADD",
  "memory_kind": "semantic_fact",
  "fact_key": "purchase.budget",
  "fact_value": {"amount": 3000, "currency": "CNY", "scope": "current_purchase"},
  "evidence_event_ids": ["evt_001"],
  "valid_from": "2026-07-21T02:00:00Z",
  "confidence": 0.96,
  "supersedes_fact_id": null,
  "reason": "customer_explicit"
}
```

`reason` is diagnostic and never substitutes for evidence. `NOOP` is not stored
as a fact. An accepted operation is applied transactionally against the current
fact version.

## 4. Durable semantic fact

Required columns:

```text
id, fact_uid, tenant_id, subject_id, fact_key, fact_value_json, normalized_value,
source_type, confidence, valid_from, valid_to, recorded_at, status,
supersedes_fact_id, version, created_by, created_at, updated_at
```

`fact_uid` identifies one fact lineage and remains stable across versions. This
allows a subject to have multiple simultaneous facts under a multi-valued key,
such as several product interests, while `version` remains unique within each
lineage. A `SUPERSEDE` operation creates the next version with the same
`fact_uid` and points `supersedes_fact_id` to the prior row.

Evidence is normalized through a fact-to-event link table. At least one valid
link is required for every active fact.

Allowed statuses:

```text
active, superseded, disputed, rejected, deleted
```

Initial source reliability order:

```text
verified_business_system > manual_customer_correction > customer_explicit
> verified_contact_provider > human_agent_annotation > model_inference
> legacy_profile
```

The ordering is a validation input, not a universal automatic overwrite rule.
For example, an order system can resolve payment status but cannot override a
customer's stated communication preference.

## 5. Fact-key catalog

WP1 must implement a typed registry for at least these keys:

| Key | Value shape | Valid sources |
|---|---|---|
| `identity.display_name` | string | contact provider, customer, correction |
| `location.region` | `{country?, province?, city?}` | customer, contact provider, correction |
| `communication.preferred_detail` | enum | customer, correction |
| `communication.preferred_channel` | enum | customer, correction |
| `purchase.budget` | `{amount, currency, scope}` | customer, correction |
| `purchase.product_interest` | `{product_id?, category?, name?}` | customer behavior, customer, commerce |
| `purchase.status` | `{order_id, status}` | verified commerce only |
| `service.pain_point` | `{topic, detail}` | customer, correction |
| `service.preference` | `{topic, value}` | customer, correction |
| `service.commitment` | `{owner, action, due_at?}` | episode with actor-specific evidence |

Unknown keys are rejected until the catalog and evaluation contract are updated.

## 6. Episode

Required fields:

```text
id, tenant_id, subject_id, episode_type, title, summary, outcome,
importance, started_at, ended_at, status, embedding_version, version,
created_at, updated_at
```

Episode-to-event links contain `episode_id`, `event_id`, and `position`. A
summary without at least one linked source event is invalid.

Initial episode types:

```text
product_consultation, purchase, refund, complaint, after_sales,
sales_objection, preference_expression, commitment
```

## 7. Retrieval context

The only Memory 2.0 structure supplied to reply generation is:

```json
{
  "schema_version": "memory_context.v1",
  "subject_id": "subject_01",
  "as_of": "2026-07-21T02:10:00Z",
  "current_facts": [],
  "relevant_episodes": [],
  "working_state": {},
  "verified_business_facts": [],
  "unresolved_conflicts": [],
  "unknowns": [],
  "evidence": []
}
```

The context builder enforces the content budget and must not expose unrelated
sensitive fields merely because they are present in the profile.

## 8. Source policy

| Claim | Accepted authority | Explicit rejection |
|---|---|---|
| Payment/refund/fulfillment | verified commerce or tool result | customer guess, assistant text |
| Inventory/price/entitlement | verified catalog or business tool | memory summary, assistant text |
| Customer preference | customer statement or correction | assistant recommendation |
| Pain point/objection | customer statement or correction | unsupported model summary |
| Contact field | authorized provider, customer, correction | model inference |
| Assistant commitment | assistant/human source event | reclassified customer fact |

## 9. Evaluation dataset contract

The seed dataset is JSONL. Each row contains:

```text
schema_version, id, category, description, tenant_id, subject_id,
events, optional distractor_events, optional query, expected
```

Every expected operation identifies evidence present in `events`. Retrieval IDs
must refer to events in the row. Cross-tenant distractors may appear only in
`distractor_events`. Synthetic examples use non-identifying content and must not
contain phone numbers, email addresses, access tokens, or real external IDs.

Initial release gates:

| Metric | Gate |
|---|---:|
| Extraction precision | 0.95 |
| Extraction recall | 0.85 |
| Operation F1 | 0.90 |
| Evidence grounding | 1.00 |
| Retrieval Recall@5 | 0.90 |
| Temporal operation accuracy | 0.85 |
| Verified-business false memory | 0 |
| Cross-tenant retrieved events | 0 |
| Deletion residue | 0 |
