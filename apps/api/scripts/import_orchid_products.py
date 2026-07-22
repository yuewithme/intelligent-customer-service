import argparse
import json
from pathlib import Path
import sys

from app.domains.catalog.orchid_products.excel_importer import build_import_payload, payload_to_json
from app.domains.catalog.orchid_products.repository import replace_orchid_product_library


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Import orchid product knowledge Excel.")
    parser.add_argument("excel_path", help="Path to orchid product Excel file.")
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Write cleaned data to DATABASE_URL. Without this flag, only prints a preview.",
    )
    parser.add_argument(
        "--preview-limit",
        type=int,
        default=3,
        help="Rows per section to print in dry-run preview.",
    )
    parser.add_argument(
        "--preview-output",
        type=Path,
        default=None,
        help="Optional JSON file path for dry-run preview.",
    )
    args = parser.parse_args()

    payload = build_import_payload(args.excel_path)
    if args.commit:
        result = replace_orchid_product_library(payload)
        result.pop("session_factory", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    preview = payload_to_json(payload, limit=args.preview_limit)
    if args.preview_output:
        args.preview_output.write_text(preview, encoding="utf-8")
    print(preview)


if __name__ == "__main__":
    main()
