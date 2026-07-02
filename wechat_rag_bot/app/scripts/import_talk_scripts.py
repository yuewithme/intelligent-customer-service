import argparse
import json

from app.talk_script.excel_importer import import_talk_script_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="Import deterministic talk scripts.")
    parser.add_argument("excel_path", help="Path to talk script Excel file.")
    args = parser.parse_args()
    result = import_talk_script_excel(args.excel_path)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
