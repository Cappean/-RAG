"""Aggregate implemented public module routers under the application API prefix."""

from fastapi import APIRouter

from app.modules.health import router as health_router
from app.worker.router import router as worker_router

router = APIRouter()
router.include_router(health_router)
router.include_router(worker_router)
