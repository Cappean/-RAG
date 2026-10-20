"""Validated application configuration; existing environment names remain supported."""

from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[3]


def env_field(default, name: str, **kwargs):
    """Accept both deployed unprefixed keys and the older example's prefix."""
    return Field(default, validation_alias=AliasChoices(name, f"MEDICAL_{name}"), **kwargs)


class Settings(BaseSettings):
    """Secrets are excluded from representations and never logged as configuration dumps."""

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )
    host: str = env_field("127.0.0.1", "HOST")
    port: int = env_field(8000, "PORT", ge=1, le=65535)
    reload: bool = env_field(False, "RELOAD")
    log_level: str = env_field("DEBUG", "LOG_LEVEL")
    mongo_uri: SecretStr = env_field(
        SecretStr("mongodb://localhost:27017"), "MONGO_URI", repr=False
    )
    mongo_database: str = env_field("medical_consultation", "MONGO_DATABASE")
    minio_endpoint: str = env_field("localhost:9000", "MINIO_ENDPOINT")
    minio_access_key: SecretStr = env_field(SecretStr(""), "MINIO_ACCESS_KEY", repr=False)
    minio_secret_key: SecretStr = env_field(SecretStr(""), "MINIO_SECRET_KEY", repr=False)
    minio_bucket: str = env_field("medical-knowledge", "MINIO_BUCKET")
    minio_secure: bool = env_field(False, "MINIO_SECURE")
    milvus_uri: str = env_field("http://localhost:19530", "MILVUS_URI", repr=False)
    milvus_token: SecretStr = env_field(SecretStr(""), "MILVUS_TOKEN", repr=False)
    milvus_collection: str = env_field("medical_chunks", "MILVUS_COLLECTION")
    deepseek_base_url: str = env_field("https://api.deepseek.com", "DEEPSEEK_BASE_URL")
    deepseek_api_key: SecretStr = env_field(SecretStr(""), "DEEPSEEK_API_KEY", repr=False)
    deepseek_model: str = env_field("deepseek-chat", "DEEPSEEK_MODEL")
    embedding_model_path: str = env_field("BAAI/bge-m3", "EMBEDDING_MODEL_PATH")
    embedding_device: str = env_field("cpu", "EMBEDDING_DEVICE", pattern="^cpu$")
    reranker_enabled: bool = env_field(False, "RERANKER_ENABLED")
    mineru_api_key: SecretStr = env_field(SecretStr(""), "MINERU_API_KEY", repr=False)
    mineru_output_dir: Path = env_field(BACKEND_ROOT / "data" / "mineru", "MINERU_OUTPUT_DIR")
    worker_concurrency: int = env_field(1, "WORKER_CONCURRENCY", ge=1, le=8)
    worker_queue_capacity: int = env_field(32, "WORKER_QUEUE_CAPACITY", ge=1)
    worker_completed_retention: int = env_field(256, "WORKER_COMPLETED_RETENTION", ge=1)
    worker_shutdown_seconds: float = env_field(10, "WORKER_SHUTDOWN_SECONDS", gt=0)
    cors_origins: list[str] = env_field(
        ["http://localhost:5173", "http://127.0.0.1:5173"], "CORS_ORIGINS"
    )
    # Unconfirmed business policies remain closed, rather than inferred from examples.
    allow_unlinked_conversations: bool = env_field(False, "ALLOW_UNLINKED_CONVERSATIONS")
    allow_ungrounded_answers: bool = env_field(False, "ALLOW_UNGROUNDED_ANSWERS")
    external_services_enabled: bool = env_field(True, "EXTERNAL_SERVICES_ENABLED")
    chunk_target_tokens: int = env_field(500, "CHUNK_TARGET_TOKENS", ge=20)
    chunk_max_tokens: int = env_field(800, "CHUNK_MAX_TOKENS", ge=40)
    chunk_overlap_tokens: int = env_field(80, "CHUNK_OVERLAP_TOKENS", ge=0)
    embedding_max_tokens: int = env_field(1024, "EMBEDDING_MAX_TOKENS", ge=800, le=8192)
    # 上传按分块落盘处理，上限不再受内存约束；与 MinerU 的 200MB 单文件限制持平。
    upload_max_bytes: int = env_field(200 * 1024 * 1024, "UPLOAD_MAX_BYTES", ge=1)
    access_url_seconds: int = env_field(900, "ACCESS_URL_SECONDS", ge=30, le=3600)
    rag_top_k: int = env_field(6, "RAG_TOP_K", ge=1, le=30)
    rag_history_messages: int = env_field(12, "RAG_HISTORY_MESSAGES", ge=0)
    rag_evidence_chars: int = env_field(16000, "RAG_EVIDENCE_CHARS", ge=1000)
    generation_max_tokens: int = env_field(1600, "GENERATION_MAX_TOKENS", ge=100)
    generation_timeout_seconds: float = env_field(180, "GENERATION_TIMEOUT_SECONDS", gt=0)
    max_message_chars: int = env_field(8000, "MAX_MESSAGE_CHARS", ge=1)
    # 标题只生成一次；超时后回退首句截取，绝不阻塞回答。
    title_max_chars: int = env_field(24, "TITLE_MAX_CHARS", ge=4, le=60)
    title_timeout_seconds: float = env_field(6, "TITLE_TIMEOUT_SECONDS", gt=0, le=30)
    rag_query_tokens: int = env_field(1024, "RAG_QUERY_TOKENS", ge=10)

    @field_validator("mineru_output_dir", mode="before")
    @classmethod
    def default_output_directory(cls, value):
        return value or BACKEND_ROOT / "data" / "mineru"

    @model_validator(mode="after")
    def compatible_budgets(self):
        """Reject impossible budgets before a document task consumes resources."""
        if not self.chunk_target_tokens <= self.chunk_max_tokens <= self.embedding_max_tokens:
            raise ValueError("chunk token budgets must fit embedding_max_tokens")
        if self.chunk_overlap_tokens >= self.chunk_max_tokens:
            raise ValueError("chunk overlap must be smaller than the maximum chunk")
        if self.rag_query_tokens > self.embedding_max_tokens:
            raise ValueError("retrieval query token budget exceeds encoder limit")
        if self.reranker_enabled:
            raise ValueError("Reranker is outside the current Demo scope; keep it disabled")
        return self


@lru_cache
def get_settings() -> Settings:
    """Load once per process; tests may inject Settings directly into create_app."""
    return Settings()
