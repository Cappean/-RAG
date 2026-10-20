"""Patient orchestration with public errors and content-free diagnostic logs."""

from datetime import UTC, datetime

from bson import ObjectId

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.modules.patient.model import patient_view, storage_fields
from app.modules.patient.schema import PatientInput

logger = get_logger(__name__)


def patient_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise AppError("INVALID_PATIENT_ID", "患者标识无效", 422)
    return ObjectId(value)


class PatientService:
    def __init__(self, repository):
        self.repository = repository

    async def list(self):
        records = await self.repository.list()
        logger.debug("patients read count=%s", len(records))
        return [patient_view(record) for record in records]

    async def get(self, identifier: str):
        record = await self.repository.get(patient_id(identifier))
        if record is None:
            raise AppError("PATIENT_NOT_FOUND", "患者不存在", 404)
        return patient_view(record)

    async def create(self, data: PatientInput):
        now = datetime.now(UTC)
        record = {
            "_id": ObjectId(),
            "ownerId": None,
            **storage_fields(data),
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repository.insert(record)
        logger.info("patient created patient_id=%s", record["_id"])
        return patient_view(record)

    async def update(self, identifier: str, data: PatientInput):
        await self.get(identifier)
        fields = {**storage_fields(data), "updatedAt": datetime.now(UTC)}
        await self.repository.replace_fields(patient_id(identifier), fields)
        logger.info("patient updated patient_id=%s", identifier)
        return await self.get(identifier)
