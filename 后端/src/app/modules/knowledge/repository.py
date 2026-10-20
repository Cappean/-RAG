"""Async MongoDB access; cross-storage workflows belong to KnowledgeService."""

from datetime import UTC, datetime

from bson import ObjectId
from pymongo import ReturnDocument

from app.core.loggingUtils import get_logger
from app.modules.knowledge.model import object_id

logger = get_logger(__name__)


class KnowledgeRepository:
    def __init__(self, database):
        self.documents = database["documents"]
        self.chunks = database["chunks"]

    async def ensure_indexes(self):
        # The unique hash protects deduplication even when two uploads race.
        await self.documents.create_index("contentHash", unique=True)
        await self.chunks.create_index([("documentId", 1), ("chunkIndex", 1)], unique=True)

    async def by_hash(self, digest):
        return await self.documents.find_one({"contentHash": digest})

    async def get(self, document_id):
        return await self.documents.find_one({"_id": object_id(document_id)})

    async def create(self, record):
        await self.documents.insert_one(record)
        logger.debug("document metadata inserted document_id=%s", record["_id"])
        return record

    async def list(self, status=None):
        query = {"status": status} if status else {}
        return await self.documents.find(query).sort([("createdAt", -1), ("_id", -1)]).to_list(None)

    async def update(self, document_id, **values):
        values["updatedAt"] = datetime.now(UTC)
        await self.documents.update_one({"_id": object_id(document_id)}, {"$set": values})

    async def claim_failed(self, document_id):
        """Atomic transition prevents duplicate manual processing jobs."""
        return await self.documents.find_one_and_update(
            {"_id": object_id(document_id), "status": "failed"},
            {"$set": {"status": "processing", "updatedAt": datetime.now(UTC)}},
            return_document=ReturnDocument.AFTER,
        )

    async def recover_interrupted(self):
        result = await self.documents.update_many(
            {"status": "processing"},
            {"$set": {"status": "failed", "updatedAt": datetime.now(UTC)}},
        )
        logger.info("interrupted document recovery failed_count=%s", result.modified_count)
        return result.modified_count

    async def replace_chunks(self, document_id, chunks):
        await self.chunks.delete_many({"documentId": object_id(document_id)})
        if chunks:
            await self.chunks.insert_many(chunks)

    async def ready_document_ids(self):
        return [
            str(row["_id"])
            for row in await self.documents.find({"status": "ready"}, {"_id": 1}).to_list(None)
        ]

    async def evidence(self, chunk_ids):
        ids = [ObjectId(value) for value in chunk_ids if ObjectId.is_valid(value)]
        chunks = await self.chunks.find({"_id": {"$in": ids}}).to_list(None)
        document_ids = list({row["documentId"] for row in chunks})
        documents = {
            row["_id"]: row
            for row in await self.documents.find(
                {"_id": {"$in": document_ids}, "status": "ready"}
            ).to_list(None)
        }
        found = {}
        for row in chunks:
            doc = documents.get(row["documentId"])
            if doc:
                found[str(row["_id"])] = {
                    "chunkId": str(row["_id"]),
                    "documentId": str(doc["_id"]),
                    "documentName": doc["name"],
                    "content": row["content"],
                    "headingPath": row["headingPath"],
                    "sourceLocations": row["sourceLocations"],
                }
        return [found[value] for value in chunk_ids if value in found]
