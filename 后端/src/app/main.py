"""Application factory; shared resources start and stop only within lifespan."""

import re
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.common.context import request_id
from app.core.config import Settings, get_settings
from app.core.errors import register_error_handlers
from app.core.loggingUtils import get_logger, setup_logging
from app.infrastructure.files import router as files_router
from app.infrastructure.services import close, initialize
from app.modules.consultation.router import router as consultation_router
from app.modules.knowledge.router import router as knowledge_router
from app.modules.patient.router import router as patient_router
from app.modules.router import router
from app.worker.handlers import HANDLERS
from app.worker.queue import MemoryTaskQueue
from app.worker.runner import Worker

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        setup_logging(settings.log_level)
        queue = MemoryTaskQueue(settings.worker_queue_capacity, settings.worker_completed_retention)
        worker = Worker(queue, dict(HANDLERS), settings.worker_concurrency)
        application.state.task_queue, application.state.worker = queue, worker
        resources = await initialize(application, settings, queue, worker)
        await worker.start()
        logger.info("API started; liveness does not assert external service readiness")
        try:
            yield
        finally:
            await worker.stop(settings.worker_shutdown_seconds)
            if resources:
                # Jobs still queued during shutdown must not remain processing in MongoDB.
                try:
                    await application.state.services.knowledge.recover_interrupted()
                except Exception as exc:
                    logger.warning("shutdown recovery failed exception_type=%s", type(exc).__name__)
            await close(resources)

    application = FastAPI(title="医疗智能问诊 Demo", lifespan=lifespan)
    application.state.settings = settings
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    register_error_handlers(application)

    @application.middleware("http")
    async def trace_request(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        identifier = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid4().hex
        request.state.request_id = identifier
        token = request_id.set(identifier)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = identifier
            logger.debug(
                "request complete request_id=%s method=%s path=%s status=%s ms=%.1f",
                identifier,
                request.method,
                request.url.path,
                response.status_code,
                (perf_counter() - started) * 1000,
            )
            return response
        finally:
            request_id.reset(token)

    application.include_router(router, prefix="/api")
    for business_router in (patient_router, consultation_router, knowledge_router, files_router):
        application.include_router(business_router, prefix="/api")
    return application


app = create_app()


def api() -> None:
    """CLI deliberately uses one process because task records are process-local."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    # 复用现有配置，以单进程启动 FastAPI。
    api()