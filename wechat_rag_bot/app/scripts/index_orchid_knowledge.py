import argparse
import asyncio
import json

from app.orchid_products.knowledge_index import index_orchid_knowledge_chunks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate embeddings for orchid_knowledge_chunks."
    )
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument(
        "--sync-existing",
        action="store_true",
        help="Also upsert rows that already have embedding_json to Qdrant.",
    )
    args = parser.parse_args()

    result = asyncio.run(
        index_orchid_knowledge_chunks(
            batch_size=args.batch_size,
            sync_existing=args.sync_existing,
        )
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
