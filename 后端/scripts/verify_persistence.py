"""Read-only acceptance audit after API restart and marked PDF reindexing."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from verify_demo import require


async def main(reports: list[Path], directory: Path):
    result = {"result": "failed", "checkedAt": datetime.now(UTC).isoformat(), "sources": []}
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", trust_env=False) as client:
            for source in reports:
                prior = json.loads(await asyncio.to_thread(source.read_text, encoding="utf-8"))
                require(prior["result"] == "passed", "source_verification_passed")
                history = await client.get(f"/api/conversations/{prior['conversationId']}/messages")
                history.raise_for_status()
                messages = history.json()
                require(len(messages) == 4, "four_history_messages_after_restart")
                require(
                    all(item["status"] == "completed" for item in messages), "history_completed"
                )
                answers = [item for item in messages if item["role"] == "assistant"]
                for answer, turn in zip(answers, prior["turns"], strict=True):
                    require(answer["id"] == turn["messageId"], "same_persisted_message_id")
                    require(len(answer["content"]) == turn["contentChars"], "same_answer_length")
                    require(answer["citations"], "historical_citation_snapshots_retained")
                if prior.get("taskId"):
                    task = await client.get(f"/api/tasks/{prior['taskId']}")
                    require(task.status_code == 404, "old_in_memory_task_not_persistent")
                result["sources"].append(
                    {
                        "report": source.name,
                        "conversationId": prior["conversationId"],
                        "historyCount": len(messages),
                        "historicalSnapshotsPresent": True,
                    }
                )
        result["result"] = "passed"
    except Exception as exc:
        result["failure"] = (
            str(exc) if type(exc).__name__ == "VerificationFailed" else type(exc).__name__
        )
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    target = directory / ("persistence-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
    await asyncio.to_thread(target.write_text, json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))
    return 0 if result["result"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / "data/verification"
    raise SystemExit(asyncio.run(main(args.reports, root)))
