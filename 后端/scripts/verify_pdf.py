"""Opt-in real PDF acceptance after API upload/ingestion, with preserved test data."""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from verify_demo import read_turn, require


async def main(
    document_id: str, source: Path, page_count: int, directory: Path, task_id: str | None
):
    report = {
        "documentId": document_id,
        "startedAt": datetime.now(UTC).isoformat(),
        "result": "failed",
        "checks": {},
    }
    try:
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:8000", timeout=240, trust_env=False
        ) as client:
            deadline = time.monotonic() + 1200
            while True:
                response = await client.get(f"/api/knowledge/documents/{document_id}")
                response.raise_for_status()
                document = response.json()
                if document["status"] != "processing":
                    break
                require(time.monotonic() < deadline, "pdf_ingestion_deadline")
                await asyncio.sleep(5)
            require(document["status"] == "ready", "pdf_ready")
            if task_id:
                response = await client.get(f"/api/tasks/{task_id}")
                response.raise_for_status()
                task = response.json()
                require(task["status"] == "succeeded", "pdf_task_succeeded")
                report.update(taskId=task_id, ingestionResult=task["result"])
            patient = (
                await client.post(
                    "/api/patients", json={"name": "DEMO-PDF-技术验证对象-非真实患者"}
                )
            ).json()
            conversation = (
                await client.post(
                    "/api/conversations",
                    json={"patientId": patient["id"], "title": "DEMO-PDF-患者手册知识验证"},
                )
            ).json()
            report.update(patientId=patient["id"], conversationId=conversation["id"])
            questions = [
                "这是公开患者手册的知识检索验证，不涉及真实患者诊疗。胃癌手册中对胃镜检查和病理诊断有何说明？请只根据所提供证据回答并注明编号。",
                "继续刚才的手册问题：病理检查为什么重要？若证据不能充分说明，请直接说明不足，不推荐个体治疗。",
            ]
            turns, citations = [], []
            for question in questions:
                turn, evidence = await read_turn(client, conversation["id"], question)
                turns.append(turn)
                citations.extend(item for item in evidence if item["documentId"] == document_id)
            require(citations, "pdf_retrieved")
            require(any(item["sourceLocations"] for item in citations), "pdf_locations")
            for item in citations:
                require(
                    not any(
                        marker in item["quote"]
                        for marker in (
                            "目录\n1 关于本文",
                            "10 副作用处理 32",
                            "11.7.2 可以吃海参吗",
                        )
                    ),
                    "known_pdf_toc_not_retrieved",
                )
                for location in item["sourceLocations"]:
                    require(1 <= location["pageNumber"] <= page_count, "pdf_page_within_source")
                    box = location.get("bbox")
                    if box:
                        require(
                            len(box) == 4 and all(0 <= value <= 1 for value in box),
                            "pdf_normalized_bbox",
                        )
            access = (await client.get(f"/api/knowledge/documents/{document_id}/access-url")).json()
            original = await client.get(access["url"])
            source_bytes = await asyncio.to_thread(source.read_bytes)
            require(
                original.status_code == 200
                and hashlib.sha256(original.content).digest()
                == hashlib.sha256(source_bytes).digest(),
                "pdf_original_exact_bytes",
            )
            partial = await client.get(access["url"], headers={"Range": "bytes=0-4"})
            require(partial.status_code == 206 and partial.content == b"%PDF-", "pdf_range")
            history = (await client.get(f"/api/conversations/{conversation['id']}/messages")).json()
            require(
                len(history) == 4 and all(m["status"] == "completed" for m in history),
                "pdf_multiturn_history_persisted",
            )
            report.update(
                result="passed",
                turns=turns,
                citationCount=len(citations),
                historyCount=len(history),
                sampleLocations=citations[0]["sourceLocations"],
            )
            report["checks"].update(
                pdfReady=True,
                pdfRetrieved=True,
                sourceExactBytes=True,
                range206=True,
                twoRealStreamingTurns=True,
                historyPersisted=True,
                knownTocNotRetrieved=True,
            )
    except Exception as exc:
        # Public verification labels only; never print answer text or signed URLs.
        report["failure"] = type(exc).__name__
        if type(exc).__name__ == "VerificationFailed":
            report["failure"] = str(exc)
    report["finishedAt"] = datetime.now(UTC).isoformat()
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    target = directory / ("pdf-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + ".json")
    await asyncio.to_thread(
        target.write_text, json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--pages", required=True, type=int)
    parser.add_argument("--task-id", help="Optional current-process ingestion task to verify")
    args = parser.parse_args()
    if args.pages < 1 or not args.source.is_file():
        parser.error("--source must exist and --pages must be positive")
    report_directory = Path(__file__).resolve().parents[1] / "data" / "verification"
    raise SystemExit(
        asyncio.run(main(args.document_id, args.source, args.pages, report_directory, args.task_id))
    )
