import argparse
import asyncio
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.services.llm_service import generate_answer, get_model_config


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_jsonl_if_exists(path: Path) -> list[dict[str, Any]]:
    return load_jsonl(path) if path.exists() else []


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def choose_pending_items(
    items: list[dict[str, Any]], existing_by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if item["id"] not in existing_by_id
        or existing_by_id[item["id"]].get("error")
    ]


def select_run_stages(*, chat_only: bool, judge_only: bool) -> tuple[bool, bool]:
    if chat_only and judge_only:
        raise ValueError("--chat-only and --judge-only cannot be used together")
    return (not judge_only, not chat_only)


async def run_chat_stage(
    *,
    runner,
    client,
    singles: list[dict[str, Any]],
    multis: list[dict[str, Any]],
    raw_path: Path,
    raw_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    completed_ids: set[str] = set()
    tasks = [runner.run_single(client, item) for item in singles]
    tasks.extend(runner.run_multi(client, item) for item in multis)
    for task in asyncio.as_completed(tasks):
        result = await task
        raw_by_id[result["id"]] = result
        append_jsonl(raw_path, result)
        completed_ids.add(result["id"])
    return completed_ids


def build_single_message(item: dict[str, Any]) -> str:
    conversation = item["conversation"]
    current = conversation[-1]["content"]
    context = [turn["content"] for turn in conversation[:-1]]
    if not context:
        return current
    return (
        "【已知对话背景】\n"
        + "\n".join(context)
        + "\n\n【客户当前消息】\n"
        + current
    )


def parse_judge_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return json.loads(text)


def _group_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [float(row["score"]) for row in rows]
    critical = sum(bool(row.get("critical_error")) for row in rows)
    return {
        "count": len(rows),
        "average_score": round(sum(scores) / len(scores), 2),
        "median_score": round(statistics.median(scores), 2),
        "critical_error_rate": round(critical / len(rows), 4),
    }


def aggregate_scores(rows: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [row for row in rows if isinstance(row.get("score"), (int, float))]
    if not valid:
        return {"overall": {"count": 0}, "by_subset": {}, "by_capability": {}}
    subsets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    capabilities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in valid:
        subsets[row["subset"]].append(row)
        capabilities[row["primary_capability"]].append(row)
    return {
        "overall": _group_summary(valid),
        "by_subset": {
            key: _group_summary(value) for key, value in sorted(subsets.items())
        },
        "by_capability": {
            key: _group_summary(value)
            for key, value in sorted(capabilities.items())
        },
    }


class EvaluationRunner:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        kb_id: str,
        concurrency: int,
    ) -> None:
        self.endpoint = f"{base_url.rstrip('/')}/api/v1/chat"
        self.api_key = api_key
        self.kb_id = kb_id
        self.semaphore = asyncio.Semaphore(concurrency)

    async def _chat(
        self,
        client: httpx.AsyncClient,
        *,
        item_id: str,
        user_id: str,
        message: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "channel": "api",
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "kb_id": self.kb_id,
            "metadata": {
                "evaluation_id": item_id,
                "skip_customer_record": True,
            },
        }
        started = time.perf_counter()
        async with self.semaphore:
            response = await client.post(
                self.endpoint,
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
        latency_ms = round((time.perf_counter() - started) * 1000)
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise RuntimeError(f"chat API error: {body}")
        data = body["data"]
        return {
            "answer": data["answer"],
            "session_id": data["session_id"],
            "route": data.get("route"),
            "intent": data.get("intent", {}),
            "sources": data.get("sources", []),
            "need_human": data.get("need_human", False),
            "next_action": data.get("next_action"),
            "trace_id": data.get("trace_id"),
            "latency_ms": latency_ms,
        }

    async def run_single(
        self, client: httpx.AsyncClient, item: dict[str, Any]
    ) -> dict[str, Any]:
        item_id = item["id"]
        try:
            result = await self._chat(
                client,
                item_id=item_id,
                user_id=f"eval_{item_id}",
                message=build_single_message(item),
            )
            return {
                "id": item_id,
                "subset": "single_turn",
                "source_case": item["source_case"],
                "primary_capability": item["primary_capability"],
                "responses": [result],
                "error": None,
            }
        except Exception as exc:
            return {
                "id": item_id,
                "subset": "single_turn",
                "source_case": item["source_case"],
                "primary_capability": item["primary_capability"],
                "responses": [],
                "error": str(exc),
            }

    async def run_multi(
        self, client: httpx.AsyncClient, item: dict[str, Any]
    ) -> dict[str, Any]:
        item_id = item["id"]
        responses = []
        session_id = None
        try:
            for index, customer_turn in enumerate(item["customer_turns"]):
                message = customer_turn
                if index == 0 and item.get("initial_context"):
                    message = (
                        "【已知客户背景】\n"
                        + item["initial_context"]
                        + "\n\n【客户当前消息】\n"
                        + customer_turn
                    )
                result = await self._chat(
                    client,
                    item_id=item_id,
                    user_id=f"eval_{item_id}",
                    session_id=session_id,
                    message=message,
                )
                session_id = result["session_id"]
                responses.append(
                    {
                        "customer": customer_turn,
                        **result,
                    }
                )
            return {
                "id": item_id,
                "subset": "multi_turn",
                "source_case": item["source_case"],
                "primary_capability": "MULTI",
                "responses": responses,
                "error": None,
            }
        except Exception as exc:
            return {
                "id": item_id,
                "subset": "multi_turn",
                "source_case": item["source_case"],
                "primary_capability": "MULTI",
                "responses": responses,
                "error": str(exc),
            }


def build_judge_prompt(
    item: dict[str, Any],
    result: dict[str, Any],
    protocol: str,
) -> str:
    return f"""你是严格的AI客服评测裁判。

请只依据评测题、被测回复和裁判协议评分，不使用外部事实。
只输出合法JSON，不要Markdown代码块或额外说明。

【裁判协议】
{protocol}

【评测题】
{json.dumps(item, ensure_ascii=False)}

【被测系统回复】
{json.dumps(result["responses"], ensure_ascii=False)}
"""


async def judge_result(
    item: dict[str, Any],
    result: dict[str, Any],
    protocol: str,
    semaphore: asyncio.Semaphore,
    attempts: int = 1,
) -> dict[str, Any]:
    base = {
        "id": result["id"],
        "subset": result["subset"],
        "source_case": result["source_case"],
        "primary_capability": result["primary_capability"],
    }
    if result.get("error"):
        return {
            **base,
            "score": None,
            "critical_error": False,
            "judge_error": f"system_error: {result['error']}",
        }
    last_error: Exception | None = None
    for _ in range(max(1, attempts)):
        try:
            async with semaphore:
                response = await generate_answer(
                    build_judge_prompt(item, result, protocol),
                    purpose="review",
                )
            judged = parse_judge_json(response["answer"])
            score = float(judged["score"])
            if not 0 <= score <= 100:
                raise ValueError(f"score out of range: {score}")
            return {
                **base,
                **judged,
                "score": score,
                "judge_error": None,
            }
        except Exception as exc:
            last_error = exc
    return {
        **base,
        "score": None,
        "critical_error": False,
        "judge_error": f"judge failed after {max(1, attempts)} attempts: {last_error}",
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def write_report(
    path: Path,
    summary: dict[str, Any],
    scores: list[dict[str, Any]],
    *,
    system_model: str,
    judge_model: str,
    skipped_boundary: int,
) -> None:
    overall = summary["overall"]
    failed = [row for row in scores if row.get("score") is None]
    low = sorted(
        (row for row in scores if isinstance(row.get("score"), (int, float)) and row["score"] < 60),
        key=lambda row: row["score"],
    )
    lines = [
        "# AI兰花销售助手基线评测报告",
        "",
        f"- 被测模型：`{system_model}`",
        f"- 裁判模型：`{judge_model}`",
        f"- 已评分题目：{overall.get('count', 0)}",
        f"- 平均分：{overall.get('average_score', 'N/A')}",
        f"- 中位数：{overall.get('median_score', 'N/A')}",
        f"- 严重错误率：{overall.get('critical_error_rate', 'N/A')}",
        f"- 执行或裁判失败：{len(failed)}",
        f"- 边界题跳过：{skipped_boundary}（系统暂不消费评测tool_state）",
        "",
        "## 子集得分",
        "",
        "| 子集 | 题数 | 平均分 | 严重错误率 |",
        "|---|---:|---:|---:|",
    ]
    for key, value in summary.get("by_subset", {}).items():
        lines.append(
            f"| {key} | {value['count']} | {value['average_score']} | {value['critical_error_rate']} |"
        )
    lines.extend(
        [
            "",
            "## 能力得分",
            "",
            "| 主能力 | 题数 | 平均分 | 严重错误率 |",
            "|---|---:|---:|---:|",
        ]
    )
    for key, value in summary.get("by_capability", {}).items():
        lines.append(
            f"| {key} | {value['count']} | {value['average_score']} | {value['critical_error_rate']} |"
        )
    lines.extend(["", "## 低于60分", ""])
    if low:
        lines.extend(
            f"- `{row['id']}`：{row['score']}分；{row.get('brief_reason', '')}"
            for row in low
        )
    else:
        lines.append("- 无")
    lines.extend(["", "## 未评分项目", ""])
    if failed:
        lines.extend(
            f"- `{row['id']}`：{row.get('judge_error', 'unknown error')}"
            for row in failed
        )
    else:
        lines.append("- 无")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    dataset_dir = Path(args.dataset_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    singles = load_jsonl(dataset_dir / "single_turn.jsonl")
    multis = load_jsonl(dataset_dir / "multi_turn.jsonl")
    boundary = load_jsonl(dataset_dir / "boundary.jsonl")
    run_chat, run_judge = select_run_stages(
        chat_only=getattr(args, "chat_only", False),
        judge_only=getattr(args, "judge_only", False),
    )
    resume = getattr(args, "resume", False)
    protocol = (
        (dataset_dir / "judge_protocol.md").read_text(encoding="utf-8")
        if run_judge
        else ""
    )

    settings = get_settings()
    raw_path = output_dir / "raw_responses.jsonl"
    scores_path = output_dir / "scores.jsonl"
    if run_judge and not run_chat and not raw_path.exists():
        raise FileNotFoundError(
            f"--judge-only requires existing raw responses: {raw_path}"
        )
    if not resume:
        if run_chat:
            raw_path.unlink(missing_ok=True)
        if run_judge:
            scores_path.unlink(missing_ok=True)
    items = [*singles, *multis]
    item_by_id = {item["id"]: item for item in items}
    raw_by_id = {row["id"]: row for row in load_jsonl_if_exists(raw_path)}
    pending_singles = choose_pending_items(singles, raw_by_id) if resume else singles
    pending_multis = choose_pending_items(multis, raw_by_id) if resume else multis
    completed_chat_ids: set[str] = set()
    if run_chat:
        runner = EvaluationRunner(
            args.base_url,
            settings.api_key,
            args.kb_id,
            args.concurrency,
        )
        timeout = httpx.Timeout(args.timeout)
        async with httpx.AsyncClient(timeout=timeout) as client:
            completed_chat_ids = await run_chat_stage(
                runner=runner,
                client=client,
                singles=pending_singles,
                multis=pending_multis,
                raw_path=raw_path,
                raw_by_id=raw_by_id,
            )

    results = [raw_by_id[item["id"]] for item in items if item["id"] in raw_by_id]
    write_jsonl(raw_path, results)
    if not run_judge:
        print(
            json.dumps(
                {
                    "chat_completed": len(results),
                    "chat_failed": sum(bool(row.get("error")) for row in results),
                },
                ensure_ascii=False,
            )
        )
        return 0 if len(results) == len(items) and not any(
            row.get("error") for row in results
        ) else 1

    judge_semaphore = asyncio.Semaphore(args.judge_concurrency)
    scores_by_id = {row["id"]: row for row in load_jsonl_if_exists(scores_path)}
    if resume:
        score_targets = [
            result
            for result in results
            if result["id"] in completed_chat_ids
            or scores_by_id.get(result["id"], {}).get("score") is None
        ]
    else:
        score_targets = results
    judge_tasks = [
        judge_result(
            item_by_id[result["id"]],
            result,
            protocol,
            judge_semaphore,
            attempts=args.judge_attempts,
        )
        for result in score_targets
    ]
    for task in asyncio.as_completed(judge_tasks):
        score = await task
        scores_by_id[score["id"]] = score
        append_jsonl(scores_path, score)
    scores = [scores_by_id[item["id"]] for item in items if item["id"] in scores_by_id]
    summary = aggregate_scores(scores)
    system_config = get_model_config("rag")
    judge_config = get_model_config("review")
    write_jsonl(scores_path, scores)
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
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary.get("overall", {}).get("count") else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--kb-id", default="kb_default")
    parser.add_argument(
        "--dataset-dir",
        default="../docs/evaluation/dataset_v1",
    )
    parser.add_argument(
        "--output-dir",
        default="../evaluation_results/baseline",
    )
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--judge-concurrency", type=int, default=2)
    parser.add_argument("--judge-attempts", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120)
    stages = parser.add_mutually_exclusive_group()
    stages.add_argument("--chat-only", action="store_true")
    stages.add_argument("--judge-only", action="store_true")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def main() -> int:
    return asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
