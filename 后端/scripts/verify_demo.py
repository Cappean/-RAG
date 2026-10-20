"""Explicit real-service Demo acceptance; creates marked data and never deletes it.

Run only against the intended live API. This script is not part of pytest and
never starts services, seeds ordinary patients, or substitutes synthetic answers.
Stored evidence contains identifiers/counts/statuses, not answers or signed URLs.
"""

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from app.core.loggingUtils import get_logger, setup_logging  # noqa: E402

logger = get_logger(__name__)


class VerificationFailed(Exception):
    """Only a safe check name is carried, never a raw remote response or URL."""


def require(condition, check):
    if not condition:
        raise VerificationFailed(check)


def response_json(response, status, check):
    require(response.status_code == status, check + "_http_status")
    return response.json()


def expired_timestamp(url):
    """Make an expiry-shaped rejection probe without publishing URL/signature.

    Modifying signed fields also invalidates the signature, so this is explicitly
    not proof of authentic wall-clock expiration. A separate short-TTL URL with
    elapsed waiting is needed to verify that behavior.
    """
    parsed = urlsplit(url)
    parameters = [
        (key, str(int(time.time()) - 1) if key == "expires" else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    require(any(key == "expires" for key, _ in parameters), "presign_expiry_present")
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(parameters), parsed.fragment)
    )


async def read_turn(client, conversation_id, content):
    counts, citations, done = {}, [], None
    async with client.stream(
        "POST", f"/api/conversations/{conversation_id}/messages", json={"content": content}
    ) as response:
        require(response.status_code == 200, "sse_http_status")
        require("text/event-stream" in response.headers.get("content-type", ""), "sse_media_type")
        event, data_lines = None, []
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and event and data_lines:
                payload = json.loads("\n".join(data_lines))
                counts[event] = counts.get(event, 0) + 1
                require(
                    event != "error", "sse_terminal_error_" + str(payload.get("code", "UNKNOWN"))
                )
                if event == "citations":
                    citations = payload["citations"]
                if event == "done":
                    done = payload["message"]
                event, data_lines = None, []
    require(done is not None, "sse_done_present")
    require(counts.get("token", 0) > 0, "sse_incremental_tokens_present")
    require(done["status"] == "completed" and done["content"].strip(), "sse_completed_nonempty")
    require(done["citations"] == citations, "sse_saved_citations_equal")
    return {
        "events": counts,
        "messageId": done["id"],
        "contentChars": len(done["content"]),
    }, citations


async def verify(base_url, report, wait_seconds):
    marker = "DEMO-VERIFY-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    report.update(
        marker=marker,
        checks={},
        limitations=[
            "API acceptance does not establish browser rendering or medical answer correctness.",
            "Expired-timestamp probe changes signed data; authentic URL TTL expiration "
            "requires separate verification.",
        ],
    )
    # The source states technical facts only. It is not a medical guideline and
    # must never be presented as clinical evidence or ordinary user knowledge.
    raw = (
        f"# {marker} 技术验收资料（非医疗指南）\n\n"
        f"本资料仅用于验证 {marker} 的上传、检索、流式传输和引用定位。\n\n"
        "## 验收步骤\n\n"
        "第一步上传 UTF-8 Markdown 原文。第二步等待文档状态 ready。"
        "第三步发送问题并保存消息历史。\n\n"
        "## 定位说明\n\n"
        "原生 Markdown 的定位使用原文件的起止行号，行号从 1 开始。引用快照不保存临时签名 URL。\n\n"
        "本资料不含诊疗建议，不得用于判断疾病或推荐药物。\n"
    )
    raw_bytes, lines = raw.encode("utf-8"), raw.splitlines()
    report["sourceSha256"] = hashlib.sha256(raw_bytes).hexdigest()
    timeout = httpx.Timeout(wait_seconds, connect=10)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False) as client:
        response_json(await client.get("/api/health/live"), 200, "liveness")
        report["checks"]["apiLive"] = True
        patient = response_json(
            await client.post("/api/patients", json={"name": marker + " 测试患者"}),
            201,
            "patient_create",
        )
        report["patientId"] = patient["id"]
        uploaded = response_json(
            await client.post(
                "/api/knowledge/documents",
                files={"file": (marker + ".md", raw_bytes, "text/markdown")},
            ),
            202,
            "upload",
        )
        document_id = uploaded["document"]["id"]
        report.update(documentId=document_id, taskId=uploaded["taskId"])
        logger.info(
            "verification upload accepted document_id=%s task_id=%s",
            document_id,
            uploaded["taskId"],
        )
        started = time.monotonic()
        while True:
            document = response_json(
                await client.get(f"/api/knowledge/documents/{document_id}"), 200, "document_poll"
            )
            if document["status"] in {"ready", "failed"}:
                break
            require(time.monotonic() - started < wait_seconds, "document_processing_deadline")
            await asyncio.sleep(2)
        require(document["status"] == "ready", "document_ready")
        report["checks"]["documentReady"] = True
        if uploaded["taskId"]:
            task = response_json(
                await client.get(f"/api/tasks/{uploaded['taskId']}"), 200, "task_get"
            )
            require(task["status"] == "succeeded", "task_succeeded")
        duplicate = response_json(
            await client.post(
                "/api/knowledge/documents",
                files={"file": (marker + ".md", raw_bytes, "text/markdown")},
            ),
            202,
            "duplicate_upload",
        )
        require(
            duplicate["duplicate"]
            and duplicate["document"]["id"] == document_id
            and duplicate["taskId"] is None,
            "duplicate_no_new_task",
        )
        report["checks"]["deduplication"] = True
        conversation = response_json(
            await client.post(
                "/api/conversations",
                json={"patientId": patient["id"], "title": marker + " 技术验证"},
            ),
            201,
            "conversation_create",
        )
        conversation_id = conversation["id"]
        report["conversationId"] = conversation_id
        questions = [
            f"仅验证技术资料，不进行医疗咨询。请根据 {marker} 技术验收资料"
            "说明前三个验收步骤，并给出证据编号。",
            f"继续上一个问题：{marker} 资料的 Markdown 定位使用什么行号规则？"
            "引用快照能否保存临时签名 URL？请给出证据编号。",
        ]
        report["turns"] = []
        all_citations = []
        for question in questions:
            turn, citations = await read_turn(client, conversation_id, question)
            report["turns"].append(turn)
            all_citations.extend(item for item in citations if item["documentId"] == document_id)
        require(all_citations, "uploaded_document_retrieved")
        for citation in all_citations:
            # Splitting may join several blocks with extra blank separators.
            # Every quoted nonempty source line must still occur verbatim in raw.
            quote_lines = [line for line in citation["quote"].splitlines() if line.strip()]
            require(
                quote_lines and all(line in raw for line in quote_lines),
                "quoted_lines_verbatim_in_original",
            )
            require(citation["sourceLocations"], "citation_source_locations_present")
            for location in citation["sourceLocations"]:
                start, end = location.get("startLine"), location.get("endLine")
                require(
                    isinstance(start, int)
                    and isinstance(end, int)
                    and 1 <= start <= end <= len(lines),
                    "markdown_location_in_original_bounds",
                )
                region = "\n".join(lines[start - 1 : end])
                require(
                    any(
                        part.strip() in region
                        for part in citation["quote"].splitlines()
                        if part.strip()
                    ),
                    "markdown_location_overlaps_quote",
                )
        report["checks"]["originalQuotesAndLineLocations"] = True
        # A fresh client and fresh request prove server history persistence across
        # API reloads by consumers; process-restart persistence is a separate check.
        async with httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False) as fresh:
            history = response_json(
                await fresh.get(f"/api/conversations/{conversation_id}/messages"),
                200,
                "fresh_history",
            )
        require(len(history) == 4, "two_turn_history_count")
        require(
            [item["role"] for item in history] == ["user", "assistant", "user", "assistant"],
            "two_turn_history_order",
        )
        require(all(item["status"] == "completed" for item in history), "history_all_completed")
        require(
            [item["id"] for item in history if item["role"] == "assistant"]
            == [turn["messageId"] for turn in report["turns"]],
            "history_sse_identifiers_equal",
        )
        report["checks"]["freshClientHistory"] = True
        await verify_access(client, report)
        report["citationCount"] = len(all_citations)
        report["historyCount"] = len(history)
        report["result"] = "passed"


async def verify_access(client, report):
    """Use only the documented short-lived access URL, checking exact file hash."""
    access = response_json(
        await client.get(f"/api/knowledge/documents/{report['documentId']}/access-url"),
        200,
        "access_url",
    )
    # Signed gateway is relative to the same API base. Never log the URL.
    signed = await client.get(access["url"])
    require(signed.status_code == 200, "signed_original_http_200")
    require(
        hashlib.sha256(signed.content).hexdigest() == report["sourceSha256"],
        "signed_original_bytes_hash",
    )
    ranged = await client.get(access["url"], headers={"Range": "bytes=0-15"})
    require(ranged.status_code == 206 and ranged.content == signed.content[:16], "signed_range_206")
    rejected = await client.get(expired_timestamp(access["url"]))
    require(rejected.status_code in {400, 403}, "modified_expired_timestamp_rejected")
    report["checks"].update(
        originalAccess=True, signedRange206=True, modifiedExpiredTimestampRejected=True
    )


async def resume_access(base_url, path, report):
    """Resume a previously successful two-turn run using read-only API requests.

    Preserve the original failed report and create a new linked evidence file.
    Existing evidence is accepted only for this removed-endpoint script mismatch;
    arbitrary failed model runs cannot become passed by performing file access.
    """
    previous = json.loads(path.read_text(encoding="utf-8"))
    require(previous.get("failure") == "same_origin_original_bytes", "resume_known_script_mismatch")
    require(
        all(
            previous.get("checks", {}).get(key)
            for key in (
                "apiLive",
                "documentReady",
                "deduplication",
                "originalQuotesAndLineLocations",
                "freshClientHistory",
            )
        ),
        "resume_prior_core_checks_passed",
    )
    require(len(previous.get("turns", [])) == 2, "resume_prior_two_turns")
    report.update(previous)
    report.update(result="failed", resumedFrom=path.name, resumedAt=datetime.now(UTC).isoformat())
    report.pop("failure", None)
    async with httpx.AsyncClient(base_url=base_url, timeout=120, trust_env=False) as client:
        history = response_json(
            await client.get(f"/api/conversations/{report['conversationId']}/messages"),
            200,
            "resume_history",
        )
        require(
            len(history) == 4 and all(item["status"] == "completed" for item in history),
            "resume_persisted_two_turns_completed",
        )
        require(
            [item["id"] for item in history if item["role"] == "assistant"]
            == [turn["messageId"] for turn in report["turns"]],
            "resume_original_message_ids",
        )
        await verify_access(client, report)
        report["historyCount"] = len(history)
        report["citationCount"] = sum(
            citation["documentId"] == report["documentId"]
            for message in history
            for citation in message["citations"]
        )
        report["result"] = "passed"


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    parser.add_argument("--wait-seconds", type=int, default=1200)
    parser.add_argument(
        "--resume-report",
        type=Path,
        help="read-only completion of access checks for a prior two-turn report",
    )
    arguments = parser.parse_args()
    setup_logging(level="DEBUG")
    # HTTP libraries can log fully signed URLs at DEBUG/INFO; only application
    # summaries are emitted, and raw exception messages are never printed.
    import logging

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    report = {"startedAt": datetime.now(UTC).isoformat(), "result": "failed"}
    try:
        if arguments.resume_report:
            await resume_access(arguments.api_base.rstrip("/"), arguments.resume_report, report)
        else:
            await verify(arguments.api_base.rstrip("/"), report, arguments.wait_seconds)
    except Exception as exc:
        report["failure"] = str(exc) if isinstance(exc, VerificationFailed) else type(exc).__name__
        logger.error("real API verification failed check=%s", report["failure"])
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        directory = BACKEND_ROOT / "data" / "verification"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (
            "demo-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".json"
        )
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("real API verification result=%s evidence_file=%s", report["result"], path.name)
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
