"""Strict input contracts explicitly reject out-of-scope attachments and status."""

from pydantic import BaseModel, ConfigDict, Field


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    patientId: str | None = None
    title: str = Field(default="新问诊", min_length=1, max_length=200)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1)


class TitleInput(BaseModel):
    """手工标题的硬上限；自动标题另受 title_max_chars 约束。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=40)
