from __future__ import annotations

import argparse
import asyncio
import json

from app.domains.decisioning.services.intent_case_import_service import (
    import_intent_labeling_case,
    list_intent_labeling_cases,
)


async def _import_cases(case_ids: list[str]) -> list[dict]:
    results = []
    for case_id in case_ids:
        results.append(await import_intent_labeling_case(case_id))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a bundled case as pending intent-labeling observations."
    )
    parser.add_argument(
        "case_ids",
        nargs="+",
        help="Bundled case IDs, for example: case01 case02; use 'all' for every case.",
    )
    args = parser.parse_args()
    case_ids = (
        list_intent_labeling_cases()
        if args.case_ids == ["all"]
        else args.case_ids
    )
    results = asyncio.run(_import_cases(case_ids))
    print(
        json.dumps(
            {
                "case_count": len(results),
                "observation_count": sum(
                    result["observation_count"] for result in results
                ),
                "cases": results,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
