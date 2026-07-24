import argparse
import json

from app.domains.customers.services.memory_backfill_service import (
    run_legacy_memory_backfill,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently backfill legacy customer memory into Memory 2.0"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the migration; otherwise run an aggregate-only dry run",
    )
    parser.add_argument("--tenant-id", default=None)
    parser.add_argument(
        "--skip-extraction-jobs",
        action="store_true",
        help="Migrate raw turns without scheduling semantic extraction",
    )
    args = parser.parse_args()
    result = run_legacy_memory_backfill(
        apply=args.apply,
        tenant_id=args.tenant_id,
        enqueue_jobs=not args.skip_extraction_jobs,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
