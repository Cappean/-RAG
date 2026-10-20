"""Explicit real API failure checks; only clearly marked Demo records are created."""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from bson import ObjectId
from fastapi import FastAPI, Request

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from app.core.config import get_settings  # noqa: E402
from app.core.errors import register_error_handlers  # noqa: E402
from app.core.loggingUtils import get_logger, setup_logging  # noqa: E402
from app.infrastructure.files import router as files_router  # noqa: E402
from app.infrastructure.runtime import ObjectStore  # noqa: E402

logger = get_logger(__name__)


def check(condition, name):
    if not condition:
        raise AssertionError(name)


async def error_check(client, method, url, expected_status, code, **kwargs):
    response = await client.request(method, url, **kwargs)
    check(response.status_code == expected_status, code + "_status")
    body = response.json()["error"]
    check(body["code"] == code and body["requestId"], code + "_envelope")
    return {"status": response.status_code, "code": body["code"], "requestIdPresent": True}


async def main_api(report):
    base = "http://127.0.0.1:8000"
    marker = "DEMO-FAILURE-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    report["marker"], report["checks"] = marker, {}
    async with httpx.AsyncClient(base_url=base, timeout=200, trust_env=False) as client:
        checks = report["checks"]
        checks["invalidPatientId"] = await error_check(
            client, "GET", "/api/patients/invalid", 422, "INVALID_PATIENT_ID"
        )
        checks["missingPatient"] = await error_check(
            client, "GET", "/api/patients/000000000000000000000000", 404, "PATIENT_NOT_FOUND"
        )
        checks["invalidConversationId"] = await error_check(
            client, "GET", "/api/conversations/invalid", 422, "INVALID_CONVERSATION_ID"
        )
        checks["missingConversation"] = await error_check(
            client,
            "GET",
            "/api/conversations/000000000000000000000000",
            404,
            "CONVERSATION_NOT_FOUND",
        )
        checks["unconfirmedUnlinkedPolicy"] = await error_check(
            client, "POST", "/api/conversations", 422, "PATIENT_SELECTION_REQUIRED", json={}
        )
        patient_response = await client.post("/api/patients", json={"name": marker + " 测试患者"})
        check(patient_response.status_code == 201, "create_marked_patient")
        patient = patient_response.json()
        response = await client.post(
            "/api/conversations", json={"patientId": patient["id"], "title": marker}
        )
        check(response.status_code == 201, "create_marked_conversation")
        conversation = response.json()
        report.update(patientId=patient["id"], conversationId=conversation["id"])
        path = f"/api/conversations/{conversation['id']}/messages"
        checks["oversizedMessage"] = await error_check(
            client,
            "POST",
            path,
            422,
            "MESSAGE_TOO_LONG",
            json={"content": "x" * (get_settings().max_message_chars + 1)},
        )
        checks["attachmentsRejected"] = await error_check(
            client,
            "POST",
            path,
            422,
            "VALIDATION_ERROR",
            json={"content": "DEMO测试", "attachmentIds": ["test"]},
        )
        event_counts, assistant_id, partial_chars = {}, None, 0
        async with client.stream(
            "POST",
            path,
            json={
                "content": "仅作技术验收，不进行医疗问诊。请根据已上传DEMO-VERIFY技术资料"
                "详细说明上传、等待ready、"
                "流式回答和原文Markdown行号定位的完整流程，并分段说明每一步的验证方式，引用证据编号。"
            },
        ) as stream:
            check(stream.status_code == 200, "live_stream_200")
            event, data = None, []
            async for line in stream.aiter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].strip())
                elif line == "" and event and data:
                    payload = json.loads("\n".join(data))
                    event_counts[event] = event_counts.get(event, 0) + 1
                    if event == "assistant_created":
                        assistant_id = payload["message"]["id"]
                        # The first stream owns the turn and is still open while a
                        # second real HTTP request verifies rejection before storage.
                        checks["concurrentTurnRejected"] = await error_check(
                            client,
                            "POST",
                            path,
                            409,
                            "GENERATION_CONFLICT",
                            json={"content": marker + " 并发拒绝测试"},
                        )
                    check(event != "error", "stream_error_" + str(payload.get("code", "UNKNOWN")))
                    if event == "token":
                        partial_chars += len(payload["delta"])
                        break  # aclose via context closes the real socket response.
                    event, data = None, []
        check(assistant_id and partial_chars > 0, "disconnect_after_real_token")
        started = time.monotonic()
        while True:
            response = await client.get(path)
            check(response.status_code == 200, "disconnect_history_200")
            history = response.json()
            assistant = next((item for item in history if item["id"] == assistant_id), None)
            if assistant and assistant["status"] != "generating":
                break
            check(time.monotonic() - started < 30, "disconnect_cleanup_deadline")
            await asyncio.sleep(0.25)
        check(len(history) == 2, "conflict_never_created_duplicate_messages")
        check(assistant["status"] == "failed", "disconnect_persisted_failed")
        check(len(assistant["content"]) >= partial_chars, "disconnect_persisted_partial")
        checks["realSocketDisconnect"] = {
            "status": assistant["status"],
            "partialChars": len(assistant["content"]),
            "messageCount": len(history),
            "events": event_counts,
        }
        # The next connection can read the failed turn; do not send an unrequested
        # retry, which would add a separate new round and another model charge.
        report["socketChecksFinishedAt"] = datetime.now(UTC).isoformat()
        logger.info("real HTTP failure checks finished conversation_id=%s", conversation["id"])


async def natural_expiry(report, source_report=None):
    """Real signer + real object bytes, isolated ASGI route, real elapsed clock."""
    objects = ObjectStore(get_settings())
    # Reuse an immutable object from a successful marked acceptance run. Resolve
    # it from evidence rather than embedding one machine's document ID in code.
    candidates = (
        [source_report]
        if source_report
        else sorted((BACKEND_ROOT / "data" / "verification").glob("demo-*.json"), reverse=True)
    )
    source = None
    for path in candidates:
        candidate = json.loads(path.read_text(encoding="utf-8"))
        if candidate.get("result") == "passed" and candidate.get("marker", "").startswith(
            "DEMO-VERIFY-"
        ):
            source = candidate
            break
    check(source is not None, "successful_marked_source_report_required")
    identifier = str(ObjectId(source["documentId"]))
    key = f"knowledge/{identifier}/original.md"
    url = await asyncio.to_thread(objects.presign, key, 3)
    app = FastAPI()
    app.state.objects = objects
    register_error_handlers(app)

    @app.middleware("http")
    async def identifier(request: Request, call_next):
        request.state.request_id = "DEMO-NATURAL-EXPIRY"
        return await call_next(request)

    app.include_router(files_router, prefix="/api")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://isolated", trust_env=False
    ) as client:
        response = await client.get(url)
        check(response.status_code == 200 and response.content, "short_signed_before_expiry")
        await asyncio.sleep(4)
        expired = await client.get(url)
        check(expired.status_code == 403, "authentic_elapsed_expiry_403")
        check(
            expired.json()["error"]["code"] == "ACCESS_URL_EXPIRED", "authentic_elapsed_expiry_code"
        )
    report["checks"]["naturalExpiry"] = {
        "transport": "isolated ASGI using production router and real MinIO",
        "ttlSeconds": 3,
        "waitSeconds": 4,
        "beforeStatus": 200,
        "afterStatus": 403,
        "mainApiConfigurationChanged": False,
        "sourceDocumentId": identifier,
    }


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expiry-only-report",
        type=Path,
        help="Complete isolated natural-expiry checks after successful socket checks",
    )
    parser.add_argument(
        "--source-report",
        type=Path,
        help="Successful verify_demo report identifying the marked MD object",
    )
    arguments = parser.parse_args()
    setup_logging(level="DEBUG")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    report = {"startedAt": datetime.now(UTC).isoformat(), "result": "failed"}
    try:
        if arguments.expiry_only_report:
            previous = json.loads(arguments.expiry_only_report.read_text(encoding="utf-8"))
            check(previous.get("socketChecksFinishedAt"), "previous_socket_checks_completed")
            report.update(previous)
            report.update(
                resumedFrom=arguments.expiry_only_report.name,
                resumedAt=datetime.now(UTC).isoformat(),
            )
            report.pop("failure", None)
        else:
            await main_api(report)
        await natural_expiry(report, arguments.source_report)
        report["result"] = "passed"
    except Exception as exc:
        report["failure"] = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
        logger.error("failure verification failed check=%s", report["failure"])
    finally:
        report["finishedAt"] = datetime.now(UTC).isoformat()
        directory = BACKEND_ROOT / "data" / "verification"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (
            "failures-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".json"
        )
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("failure verification result=%s evidence_file=%s", report["result"], path.name)
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
