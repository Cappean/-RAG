"""Compose business services; keep network clients owned by application lifespan."""

import asyncio
from types import SimpleNamespace

from pymongo import AsyncMongoClient

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.infrastructure.mineru import MinerUClient
from app.infrastructure.runtime import DeepSeekModel, EmbeddingModel, ObjectStore, VectorStore
from app.modules.consultation.repository import ConsultationRepository
from app.modules.consultation.service import ConsultationService
from app.modules.knowledge.repository import KnowledgeRepository
from app.modules.knowledge.service import KnowledgeService
from app.modules.patient.repository import PatientRepository
from app.modules.patient.service import PatientService
from app.modules.rag.service import RagService

logger = get_logger(__name__)


class UnavailableService:
    """Keep API liveness independent; business routes fail clearly when startup fails."""

    def __getattr__(self, name):
        async def unavailable(*args, **kwargs):
            raise AppError("DEPENDENCY_UNAVAILABLE", "依赖服务尚未就绪，请查看服务端组件日志", 503)

        return unavailable


async def initialize(application, settings, queue, worker):
    missing = UnavailableService()
    application.state.services = SimpleNamespace(
        patient=missing, consultation=missing, knowledge=missing
    )
    application.state.component_status = {"storage": "unavailable", "models": "not_loaded"}
    if not settings.external_services_enabled:
        return None
    client = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(), serverSelectionTimeoutMS=5000, tz_aware=True
    )
    model = vectors = None
    try:
        await client.admin.command("ping")
        database = client[settings.mongo_database]
        objects = ObjectStore(settings)
        vectors = await asyncio.to_thread(VectorStore, settings)
        # Creation is idempotent and schema conflicts are reported, never overwritten.
        await asyncio.to_thread(objects.initialize)
        await asyncio.to_thread(vectors.initialize)
        model, embedding = DeepSeekModel(settings), EmbeddingModel(settings)
        repository = KnowledgeRepository(database)
        await repository.ensure_indexes()
        mineru = (
            MinerUClient(
                token=settings.mineru_api_key.get_secret_value(),
                output_dir=settings.mineru_output_dir,
            )
            if settings.mineru_api_key.get_secret_value()
            else None
        )
        knowledge = KnowledgeService(
            repository, objects, vectors, embedding, queue, mineru, settings
        )
        patients = PatientService(PatientRepository(database))
        rag = RagService(embedding, vectors, knowledge, model, settings)
        consultation = ConsultationService(
            ConsultationRepository(database), patients, rag, settings
        )
        await knowledge.recover_interrupted()
        await consultation.recover()

        async def ingest(task):
            task.progress = "解析、切分及向量入库"
            return await knowledge.process_document(
                task.payload["documentId"], lambda stage: setattr(task, "progress", stage)
            )

        worker.handlers["knowledge.ingest"] = ingest
        application.state.services = SimpleNamespace(
            patient=patients, consultation=consultation, knowledge=knowledge
        )
        application.state.objects = objects
        application.state.component_status["storage"] = "ready"
        logger.info("business storage initialized database=%s", settings.mongo_database)
        return client, vectors, model
    except Exception as exc:
        logger.error("business storage initialization failed exception_type=%s", type(exc).__name__)
        await close((client, vectors, model))
        return None


async def close(resources):
    if resources:
        client, vectors, model = resources
        # Each resource gets a cleanup attempt even if another SDK's close fails.
        for label, resource in (("model", model), ("vectors", vectors), ("mongo", client)):
            if resource is None:
                continue
            try:
                if label == "vectors":
                    await asyncio.to_thread(resource.close)
                else:
                    await resource.close()
            except Exception as exc:
                logger.warning(
                    "resource close failed component=%s exception_type=%s",
                    label,
                    type(exc).__name__,
                )
