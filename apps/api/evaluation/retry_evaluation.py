import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.integrations.ai.services.llm_service import get_model_config
from evaluation.run_evaluation import (
    EvaluationRunner,
    aggregate_scores,
    judge_result,
    load_jsonl,
    write_jsonl,
    write_report,
)


def merge_by_id(
    original: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    replacements_by_id = {row["id"]: row for row in replacements}
    return [replacements_by_id.get(row["id"], row) for row in original]


async def _judge_with_retries(
    item: dict[str, Any],
    result: dict[str, Any],
    protocol: str,
    semaphore: asyncio.Semaphore,
    attempts: int,
) -> dict[str, Any]:
    judged: dict[str, Any] | None = None
    for _ in range(attempts):
        judged = await judge_result(item, result, protocol, semaphore)
        if judged.get("score") is not None:
            return judged
    assert judged is not None
    return judged


async def async_main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    singles = load_jsonl(dataset_dir / "single_turn.jsonl")
    multis = load_jsonl(dataset_dir / "multi_turn.jsonl")
    boundary = load_jsonl(dataset_dir / "boundary.jsonl")
    protocol = (dataset_dir / "judge_protocol.md").read_text(encoding="utf-8")
    items = [*singles, *multis]
    item_by_id = {item["id"]: item for item in items}

    raw_path = output_dir / "raw_responses.jsonl"
    scores_path = output_dir / "scores.jsonl"
    raw = load_jsonl(raw_path)
    old_scores = load_jsonl(scores_path)

    settings = get_settings()
    runner = EvaluationRunner(
        args.base_url,
        settings.api_key,
        args.kb_id,
        args.concurrency,
    )
    failed_results = [row for row in raw if row.get("error")]
    retried_results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(args.timeout)) as client:
        tasks = []
        for row in failed_results:
            item = item_by_id[row["id"]]
            if item["task_type"] == "single_turn":
                tasks.append(runner.run_single(client, item))
            else:
                tasks.append(runner.run_multi(client, item))
        if tasks:
            retried_results = await asyncio.gather(*tasks)
    raw = merge_by_id(raw, retried_results)
    write_jsonl(raw_path, raw)

    old_score_by_id = {row["id"]: row for row in old_scores}
    score_targets = [
        result
        for result in raw
        if result.get("error")
        or old_score_by_id.get(result["id"], {}).get("score") is None
    ]
    judge_semaphore = asyncio.Semaphore(args.judge_concurrency)
    retried_scores = await asyncio.gather(
        *[
            _judge_with_retries(
                item_by_id[result["id"]],
                result,
                protocol,
                judge_semaphore,
                args.judge_attempts,
            )
            for result in score_targets
        ]
    )
    scores = merge_by_id(old_scores, retried_scores)
    write_jsonl(scores_path, scores)
    summary = aggregate_scores(scores)
    system_config = get_model_config("rag")
    judge_config = get_model_config("review")
    (output_dir / "summary.json").write_text(
        json.dumps(
            {
                **summary,
                "system_model": f"{system_config.provider}/{system_config.model}",
                "judge_model": f"{judge_config.provider}/{judge_config.model}",
                "skipped_boundary": len(boundary),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    write_report(
        output_dir / "report.md",
        summary,
        scores,
        system_model=f"{system_config.provider}/{system_config.model}",
        judge_model=f"{judge_config.provider}/{judge_config.model}",
        skipped_boundary=len(boundary),
    )
    print(
        json.dumps(
            {
                "retried_system": len(failed_results),
                "retried_judge": len(score_targets),
                "summary": summary,
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary.get("overall", {}).get("count") else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kb-id", default="kb_default")
    parser.add_argument(
        "--dataset-dir", default="../../datasets/evaluation/suites/dataset_v1"
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--judge-concurrency", type=int, default=1)
    parser.add_argument("--judge-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
