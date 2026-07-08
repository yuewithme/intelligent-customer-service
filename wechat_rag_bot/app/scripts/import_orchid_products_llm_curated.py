import argparse
import json

from app.orchid_products.llm_curated_importer import build_llm_curated_payload
from app.orchid_products.repository import replace_orchid_product_library


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import LLM-curated orchid product rows into orchid_* tables."
    )
    parser.add_argument("excel_path")
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()

    payload = build_llm_curated_payload(args.excel_path)
    if not args.commit:
        print(json.dumps(payload.counts, ensure_ascii=False, indent=2))
        return
    result = replace_orchid_product_library(payload)
    result.pop("session_factory", None)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
