"""Persisted sequential turns with interruption-safe partial answer retention."""

import asyncio
from datetime import UTC, datetime

import anyio
from bson import ObjectId

from app.core.errors import AppError
from app.core.loggingUtils import get_logger
from app.modules.consultation.model import conversation_view, message_view

logger = get_logger(__name__)


def conversation_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise AppError("INVALID_CONVERSATION_ID", "会话标识无效", 422)
    return ObjectId(value)


class ConsultationService:
    def __init__(self, repository, patients, rag, settings):
        self.repository, self.patients, self.rag = repository, patients, rag
        self.settings = settings
        # Single-process deployment makes this atomic reservation sufficient.
        # Entries exist only during an active turn, rather than for every old chat.
        self.active: set[str] = set()

    async def recover(self):
        count = await self.repository.recover(datetime.now(UTC))
        logger.info("interrupted messages recovered failed_count=%s", count)
        return count

    async def create(self, data):
        if data.patientId:
            await self.patients.get(data.patientId)
        elif not getattr(self.settings, "allow_unlinked_conversations", False):
            raise AppError(
                "PATIENT_SELECTION_REQUIRED", "请先选择患者；无患者问诊策略尚未确认", 422
            )
        now = datetime.now(UTC)
        record = {
            "_id": ObjectId(),
            "ownerId": None,
            "patientId": ObjectId(data.patientId) if data.patientId else None,
            "title": data.title,
            "createdAt": now,
            "updatedAt": now,
        }
        await self.repository.insert(record)
        logger.info("conversation created conversation_id=%s", record["_id"])
        return conversation_view(record)

    async def list(self, patient_id=None):
        if patient_id:
            await self.patients.get(patient_id)
        records = await self.repository.list(ObjectId(patient_id) if patient_id else None)
        logger.debug("conversations read count=%s", len(records))
        return [conversation_view(record) for record in records]

    async def get(self, identifier):
        record = await self.repository.get(conversation_id(identifier))
        if record is None:
            raise AppError("CONVERSATION_NOT_FOUND", "会话不存在", 404)
        result = conversation_view(record)
        result["patient"] = (
            await self.patients.get(result["patientId"]) if result["patientId"] else None
        )
        return result

    async def history(self, identifier):
        await self.get(identifier)
        records = await self.repository.history(conversation_id(identifier))
        logger.debug("messages read conversation_id=%s count=%s", identifier, len(records))
        return [message_view(record) for record in records]

    async def begin(self, identifier, content):
        """Reserve and persist before HTTP headers so validation/conflicts are 4xx."""
        if len(content) > getattr(self.settings, "max_message_chars", 8000):
            raise AppError("MESSAGE_TOO_LONG", "消息超过允许长度", 422)
        # ObjectId accepts uppercase hex too; canonicalize before reserving so
        # alternate spellings cannot bypass the same-conversation lock.
        identifier = str(conversation_id(identifier))
        if identifier in self.active:
            raise AppError("GENERATION_CONFLICT", "本会话正在生成回答，请等待完成", 409)
        self.active.add(identifier)
        assistant = None
        try:
            conversation = await self.get(identifier)
            history = await self.repository.history(ObjectId(identifier))
            if any(item["status"] == "generating" for item in history):
                # A prior database write failure may outlive its in-process turn.
                # Never start a second answer over an unresolved persisted turn.
                raise AppError(
                    "GENERATION_CONFLICT", "本会话存在未完成回答，请稍后重试或重启服务恢复状态", 409
                )
            now = datetime.now(UTC)
            user = {
                "_id": ObjectId(),
                "conversationId": ObjectId(identifier),
                "role": "user",
                "content": content,
                "status": "completed",
                "citations": [],
                "createdAt": now,
                "updatedAt": now,
            }
            assistant = {
                **user,
                "_id": ObjectId(),
                "role": "assistant",
                "content": "",
                "status": "generating",
            }
            await self.repository.insert_message(user)
            await self.repository.insert_message(assistant)
            await self.repository.touch(ObjectId(identifier), now)
            logger.info(
                "turn started conversation_id=%s message_id=%s", identifier, assistant["_id"]
            )
            return Turn(self, identifier, assistant, user, history, conversation["patient"])
        except BaseException:
            try:
                if assistant is not None:
                    await self.repository.update_message(
                        assistant["_id"], {"status": "failed", "updatedAt": datetime.now(UTC)}
                    )
            except Exception as cleanup_error:
                # Preserve the original failure and release in-process ownership
                # even if the database is unavailable during failure cleanup.
                logger.error(
                    "turn admission cleanup failed conversation_id=%s exception_type=%s",
                    identifier,
                    type(cleanup_error).__name__,
                )
            finally:
                self.active.discard(identifier)
            raise


class Turn:
    """One owned generation; close is idempotent for stream and background cleanup."""

    def __init__(self, service, identifier, assistant, user, history, patient):
        self.service, self.identifier = service, identifier
        self.assistant, self.user, self.history, self.patient = assistant, user, history, patient
        self.closed = False

    async def close(self, status="failed"):
        if self.closed:
            return
        # Do not release ownership until persistence finishes; a failed write leaves
        # generating visible and startup recovery will classify it on restart.
        now = datetime.now(UTC)
        try:
            await self.service.repository.update_message(
                self.assistant["_id"],
                {
                    "content": self.assistant["content"],
                    "citations": self.assistant["citations"],
                    "status": status,
                    "updatedAt": now,
                },
            )
            await self.service.repository.touch(ObjectId(self.identifier), now)
            self.assistant.update(status=status, updatedAt=now)
            logger.info(
                "turn finished conversation_id=%s message_id=%s status=%s chars=%s",
                self.identifier,
                self.assistant["_id"],
                status,
                len(self.assistant["content"]),
            )
        except Exception as exc:
            logger.error(
                "message persistence failed conversation_id=%s exception_type=%s",
                self.identifier,
                type(exc).__name__,
            )
            raise AppError(
                "MESSAGE_PERSISTENCE_FAILED",
                "消息保存失败；请刷新核对历史，未完成回答将在服务重启后标记失败",
                503,
            ) from exc
        finally:
            self.closed = True
            self.service.active.discard(self.identifier)

    async def events(self):
        try:
            yield (
                "assistant_created",
                {"message": message_view(self.assistant), "userMessage": message_view(self.user)},
            )
            yield "status", {"stage": "retrieving"}
            async with asyncio.timeout(
                getattr(self.service.settings, "generation_timeout_seconds", 180)
            ):
                prepared = await self.service.rag.prepare(
                    self.user["content"], self.history, self.patient
                )
                self.assistant["citations"] = prepared["citations"]
                # Persist evidence and occasional text snapshots, never each token.
                await self.service.repository.update_message(
                    self.assistant["_id"],
                    {"citations": prepared["citations"], "updatedAt": datetime.now(UTC)},
                )
                yield (
                    "citations",
                    {"messageId": str(self.assistant["_id"]), "citations": prepared["citations"]},
                )
                yield "status", {"stage": "generating", "retrievalMs": prepared["retrievalMs"]}
                checkpoint = 0
                async for token in self.service.rag.stream(prepared):
                    self.assistant["content"] += token
                    if len(self.assistant["content"]) - checkpoint >= 512:
                        await self.service.repository.update_message(
                            self.assistant["_id"],
                            {"content": self.assistant["content"], "updatedAt": datetime.now(UTC)},
                        )
                        checkpoint = len(self.assistant["content"])
                    yield "token", {"messageId": str(self.assistant["_id"]), "delta": token}
                if not self.assistant["content"].strip():
                    raise AppError("EMPTY_MODEL_RESPONSE", "模型未返回回答，请稍后重试", 502)
            await self.close("completed")
            yield (
                "done",
                {"message": message_view(self.assistant), "retrievalMs": prepared["retrievalMs"]},
            )
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("turn disconnected conversation_id=%s", self.identifier)
            raise
        except Exception as exc:
            logger.warning(
                "turn failed conversation_id=%s exception_type=%s",
                self.identifier,
                type(exc).__name__,
            )
            try:
                await self.close()
            except Exception as persistence_error:
                # A started SSE stream still needs a public terminal failure when
                # persistence itself fails. Never claim partial text was saved.
                exc = persistence_error
            if isinstance(exc, AppError):
                code, message = exc.code, exc.message
            elif isinstance(exc, TimeoutError):
                code, message = "GENERATION_TIMEOUT", "回答生成超时，已保留部分内容"
            else:
                code, message = "GENERATION_FAILED", "检索或模型服务暂时不可用，已保留本轮消息"
            yield (
                "error",
                {"code": code, "message": message, "messageId": str(self.assistant["_id"])},
            )
        finally:
            # Starlette uses AnyIO level cancellation: asyncio.shield alone cannot
            # protect repeated awaits in an already cancelled task-group scope.
            # Shield cleanup at that boundary so disconnect saves partial content.
            with anyio.CancelScope(shield=True):
                await self.close()
