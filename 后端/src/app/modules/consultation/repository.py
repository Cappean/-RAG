"""Only this module's collections are accessed; no cross-module writes."""

from bson import ObjectId


class ConsultationRepository:
    def __init__(self, database):
        self.conversations = database["conversations"]
        self.messages = database["messages"]

    async def list(self, patient_id: ObjectId | None = None):
        query = {"patientId": patient_id} if patient_id else {}
        return (
            await self.conversations.find(query)
            .sort([("updatedAt", -1), ("_id", -1)])
            .to_list(None)
        )

    async def get(self, identifier):
        return await self.conversations.find_one({"_id": identifier})

    async def insert(self, record):
        await self.conversations.insert_one(record)

    async def history(self, identifier):
        return (
            await self.messages.find({"conversationId": identifier})
            .sort([("createdAt", 1), ("_id", 1)])
            .to_list(None)
        )

    async def insert_message(self, record):
        await self.messages.insert_one(record)

    async def update_message(self, identifier, fields):
        await self.messages.update_one({"_id": identifier}, {"$set": fields})

    async def touch(self, identifier, now):
        await self.conversations.update_one({"_id": identifier}, {"$set": {"updatedAt": now}})

    async def update_title(self, identifier, title, source):
        """改标题不触碰 updatedAt，避免重命名把会话顶到历史列表最前面。"""
        result = await self.conversations.update_one(
            {"_id": identifier}, {"$set": {"title": title, "titleSource": source}}
        )
        return result.matched_count > 0

    async def delete(self, identifier):
        """先删会话再删消息：会话不可见比遗留不可见消息更重要。"""
        result = await self.conversations.delete_one({"_id": identifier})
        if result.deleted_count == 0:
            return 0
        removed = await self.messages.delete_many({"conversationId": identifier})
        return removed.deleted_count

    async def recover(self, now):
        result = await self.messages.update_many(
            {"role": "assistant", "status": "generating"},
            {"$set": {"status": "failed", "updatedAt": now}},
        )
        return result.modified_count
