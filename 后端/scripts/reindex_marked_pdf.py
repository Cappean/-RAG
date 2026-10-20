"""Explicit one-off repair of our marked acceptance PDF after a splitter defect.

This is not a public ready-document reindex policy. Immutable originals/parser
outputs and historical citation snapshots remain preserved.
"""

import asyncio
import json
from datetime import UTC, datetime

import httpx
from bson import ObjectId
from pymongo import AsyncMongoClient

from app.core.config import Settings
from app.core.loggingUtils import get_logger, setup_logging

DOCUMENT_ID = "6ad64c82643f4a840b2aac95"
MARKER = "DEMO_real_pipeline_sample.pdf"
logger = get_logger(__name__)


async def main():
    settings = Settings()
    setup_logging(settings.log_level)
    mongo = AsyncMongoClient(settings.mongo_uri.get_secret_value())
    try:
        documents = mongo[settings.mongo_database].documents
        row = await documents.find_one({"_id": ObjectId(DOCUMENT_ID)})
        # Exact ID, explicit marker and existing parsed artifact prevent touching
        # user uploads or accidentally issuing a fresh paid parsing request.
        if not row or row.get("name") != MARKER or not row.get("parserOutputPrefix"):
            raise RuntimeError("Marked acceptance PDF/artifact guard failed")
        if row["status"] != "ready":
            raise RuntimeError("Expected terminal ready state before this one-off repair")
        result = await documents.update_one(
            {"_id": row["_id"], "status": "ready", "name": MARKER},
            {"$set": {"status": "failed", "updatedAt": datetime.now(UTC)}},
        )
        if result.modified_count != 1:
            raise RuntimeError("Repair state claim failed")
        logger.info("marked acceptance repair admitted document_id=%s", DOCUMENT_ID)
        async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", trust_env=False) as client:
            response = await client.post(f"/api/knowledge/documents/{DOCUMENT_ID}/retry")
            response.raise_for_status()
            payload = response.json()
            print(json.dumps({"documentId": DOCUMENT_ID, "taskId": payload["taskId"]}))
    finally:
        await mongo.close()


if __name__ == "__main__":
    asyncio.run(main())
