"""All patient collection reads and writes live at this boundary."""

from bson import ObjectId


class PatientRepository:
    def __init__(self, database):
        self.collection = database["patients"]

    async def list(self):
        return await self.collection.find({}).sort([("updatedAt", -1), ("_id", -1)]).to_list(None)

    async def get(self, identifier: ObjectId):
        return await self.collection.find_one({"_id": identifier})

    async def insert(self, record: dict):
        await self.collection.insert_one(record)

    async def replace_fields(self, identifier: ObjectId, fields: dict):
        await self.collection.update_one({"_id": identifier}, {"$set": fields})
