"""SSE framing and HTTP binding; no database/model behavior lives here."""

import json

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.modules.consultation.schema import ConversationInput, MessageInput, TitleInput

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", status_code=201)
async def create_conversation(data: ConversationInput, request: Request):
    return await request.app.state.services.consultation.create(data)


@router.get("")
async def list_conversations(request: Request, patientId: str | None = None):
    return await request.app.state.services.consultation.list(patientId)


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, request: Request):
    return await request.app.state.services.consultation.get(conversation_id)


@router.patch("/{conversation_id}/title")
async def rename_conversation(conversation_id: str, data: TitleInput, request: Request):
    return await request.app.state.services.consultation.rename(conversation_id, data.title)


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, request: Request):
    return await request.app.state.services.consultation.delete(conversation_id)


@router.get("/{conversation_id}/messages")
async def list_messages(conversation_id: str, request: Request):
    return await request.app.state.services.consultation.history(conversation_id)


@router.post("/{conversation_id}/messages")
async def send_message(conversation_id: str, data: MessageInput, request: Request):
    turn = await request.app.state.services.consultation.begin(conversation_id, data.content)

    async def stream():
        async for event, payload in turn.events():
            data_json = json.dumps(jsonable_encoder(payload), ensure_ascii=False)
            yield f"event: {event}\ndata: {data_json}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        background=BackgroundTask(turn.close),
    )
