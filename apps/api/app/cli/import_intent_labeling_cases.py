from __future__ import annotations

import argparse
import asyncio
import json

from app.domains.decisioning.services.intent_case_import_service import (
    import_intent_labeling_case,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import a bundled case as pending intent-labeling observations."
    )
    parser.add_argument("case_id", help="Bundled case ID, for example: case01")
    args = parser.parse_args()
    result = asyncio.run(import_intent_labeling_case(args.case_id))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
