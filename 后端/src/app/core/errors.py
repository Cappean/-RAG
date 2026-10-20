"""Safe, stable HTTP error responses without leaking clinical text or credentials."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.core.loggingUtils import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Business errors carry only deliberately public messages and safe details."""

    def __init__(self, code: str, message: str, status_code: int = 400, details=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.status_code, self.details = status_code, details


def error_response(request: Request, code: str, message: str, status: int, details=None):
    """Use the same request identifier in both body and header, including HTTP 500."""
    identifier = getattr(request.state, "request_id", "")
    return JSONResponse(
        {"error": {"code": code, "message": message, "details": details, "requestId": identifier}},
        status_code=status,
        headers={"X-Request-ID": identifier},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def business_error(request: Request, exc: AppError):
        logger.debug("request rejected request_id=%s code=%s", request.state.request_id, exc.code)
        return error_response(request, exc.code, exc.message, exc.status_code, exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # Framework errors are generic: do not reflect arbitrary exception details.
        return error_response(
            request, "HTTP_ERROR", "请求资源不存在或请求无法处理", exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"location": list(item["loc"]), "type": item["type"]} for item in exc.errors()]
        return error_response(request, "VALIDATION_ERROR", "请求参数校验失败", 422, details)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        # Exception messages/tracebacks may contain URLs, API keys or clinical content.
        logger.error(
            "request failed request_id=%s exception_type=%s",
            request.state.request_id,
            type(exc).__name__,
        )
        return error_response(request, "INTERNAL_ERROR", "服务暂时无法处理请求", 500)
