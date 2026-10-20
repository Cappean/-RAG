"""Real external adapters. SDK calls and CPU inference run in worker threads.

No adapter silently substitutes local storage or invented model results. Model loading
is lazy; importing the API never allocates the 2 GB encoder or contacts a provider.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import re
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from app.core.errors import AppError
from app.core.loggingUtils import get_logger

logger = get_logger(__name__)


class ObjectStore:
    """MinIO object keys are durable; signed URLs are generated only on demand."""

    def __init__(self, settings):
        import urllib3
        from minio import Minio

        self.bucket = settings.minio_bucket
        self._signing_key = secrets.token_bytes(32)
        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key.get_secret_value(),
            secret_key=settings.minio_secret_key.get_secret_value(),
            secure=settings.minio_secure,
            http_client=urllib3.PoolManager(
                timeout=urllib3.Timeout(connect=3, read=20), retries=False
            ),
        )

    def initialize(self):
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("object bucket created bucket=%s", self.bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream"):
        self.client.put_object(
            self.bucket, key, io.BytesIO(data), len(data), content_type=content_type
        )
        logger.debug("object stored bytes=%s", len(data))

    def get(self, key: str) -> bytes:
        response = self.client.get_object(self.bucket, key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def presign(self, key: str, expires_seconds: int = 900) -> str:
        # Validate existence, so the UI gets a clear failure before opening a viewer.
        self.client.stat_object(self.bucket, key)
        expires = int(time.time()) + expires_seconds
        signature = self._signature(key, expires)
        return "/api/objects?" + urlencode({"key": key, "expires": expires, "signature": signature})

    def _signature(self, key: str, expires: int) -> str:
        return hmac.new(self._signing_key, f"{expires}\n{key}".encode(), hashlib.sha256).hexdigest()

    def verify(self, key: str, expires: int, signature: str) -> None:
        if (
            not re.fullmatch(r"[0-9a-f]{64}", signature)
            or expires <= int(time.time())
            or not hmac.compare_digest(self._signature(key, expires), signature)
        ):
            raise AppError("ACCESS_URL_EXPIRED", "原文链接无效或已过期，请重新打开引用", 403)

    def upload_tree(self, directory: Path, prefix: str):
        import mimetypes

        root = directory.resolve()
        for path in sorted(root.rglob("*")):
            if path.is_file():
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("解析产物路径越界")
                key = prefix.rstrip("/") + "/" + path.relative_to(root).as_posix()
                self.client.fput_object(
                    self.bucket,
                    key,
                    str(path),
                    content_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                )
        logger.info("parser artifacts uploaded")

    def list(self, prefix: str) -> list[str]:
        """List all persisted parser objects; ambiguous input selection is a service error."""
        return [
            item.object_name
            for item in self.client.list_objects(self.bucket, prefix=prefix, recursive=True)
        ]


class EmbeddingModel:
    """BGE-M3's published CLS dense and learned lexical sparse heads, on CPU.

    The sparse head is deliberately loaded from sparse_linear.pt; BM25 and plain
    dense-only sentence transformers cannot implement the documented retrieval.
    One lock bounds simultaneous CPU inference and prevents concurrent lazy loads.
    """

    def __init__(self, settings):
        self.path = Path(settings.embedding_model_path)
        self._lock = threading.RLock()
        self._tokenizer = self._model = self._sparse = None
        self.max_length = getattr(settings, "embedding_max_tokens", 1024)

    def _load_tokenizer(self):
        if self._tokenizer is None:
            from transformers import AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(str(self.path), local_files_only=True)

    def count_tokens(self, text: str) -> int:
        with self._lock:
            self._load_tokenizer()
            return len(self._tokenizer.encode(text, add_special_tokens=True))

    def truncate(self, text: str, max_tokens: int = 1024) -> str:
        """Budget retrieval text only; never truncate the persisted user message."""
        with self._lock:
            self._load_tokenizer()
            ids = self._tokenizer.encode(text, add_special_tokens=False)
            return self._tokenizer.decode(ids[: max(1, max_tokens - 2)], skip_special_tokens=True)

    def encode(self, texts: list[str]) -> list[dict]:
        import torch
        from transformers import AutoModel

        with self._lock:
            self._load_tokenizer()
            if self._model is None:
                logger.info("BGE-M3 loading device=cpu sparse_head=true")
                model = (
                    AutoModel.from_pretrained(
                        str(self.path), local_files_only=True, use_safetensors=False
                    )
                    .to("cpu")
                    .eval()
                )
                sparse = torch.nn.Linear(model.config.hidden_size, 1)
                sparse.load_state_dict(
                    torch.load(
                        self.path / "sparse_linear.pt", map_location="cpu", weights_only=True
                    )
                )
                sparse.eval()
                self._model, self._sparse = model, sparse
                logger.info("BGE-M3 loaded dimensions=%s", self._model.config.hidden_size)
            rows = []
            # Small batches are intentional: 3.13 Windows CPU Demo must bound RAM.
            for text in texts:
                tokens = self._tokenizer(text, return_tensors="pt", truncation=False)
                if tokens["input_ids"].shape[1] > self.max_length:
                    raise AppError("EMBEDDING_INPUT_TOO_LONG", "向量输入超过配置长度", 422)
                with torch.inference_mode():
                    hidden = self._model(**tokens).last_hidden_state
                    dense = torch.nn.functional.normalize(hidden[:, 0], p=2, dim=-1)[0]
                    weights = torch.relu(self._sparse(hidden)).squeeze(-1)[0]
                sparse = {}
                unused = {
                    self._tokenizer.cls_token_id,
                    self._tokenizer.eos_token_id,
                    self._tokenizer.pad_token_id,
                    self._tokenizer.unk_token_id,
                }
                for token, weight in zip(
                    tokens["input_ids"][0].tolist(), weights.tolist(), strict=True
                ):
                    if token not in unused and weight > 0:
                        sparse[token] = max(sparse.get(token, 0), float(weight))
                rows.append({"denseVector": dense.tolist(), "sparseVector": sparse})
            logger.debug("BGE-M3 encoded count=%s", len(rows))
            return rows


class VectorStore:
    """Milvus dense IP + learned sparse IP, combined with rank-based fusion."""

    def __init__(self, settings):
        from pymilvus import MilvusClient

        self.collection = settings.milvus_collection
        self.client = MilvusClient(
            uri=settings.milvus_uri, token=settings.milvus_token.get_secret_value(), timeout=15
        )

    def initialize(self):
        from pymilvus import DataType

        if self.client.has_collection(self.collection):
            description = self.client.describe_collection(self.collection)
            fields = {field["name"]: field for field in description["fields"]}
            expected = {"chunkId", "documentId", "denseVector", "sparseVector"}
            compatible = expected.issubset(fields)
            expected_types = {
                "chunkId": DataType.VARCHAR,
                "documentId": DataType.VARCHAR,
                "denseVector": DataType.FLOAT_VECTOR,
                "sparseVector": DataType.SPARSE_FLOAT_VECTOR,
            }
            compatible = compatible and all(
                fields[key].get("type") == dtype for key, dtype in expected_types.items()
            )
            compatible = compatible and fields["chunkId"].get("is_primary", False)
            compatible = (
                compatible and int(fields["denseVector"].get("params", {}).get("dim", 0)) == 1024
            )
            indexed = set()
            for name in self.client.list_indexes(self.collection):
                index = self.client.describe_index(self.collection, name)
                if index.get("metric_type") == "IP":
                    indexed.add(index.get("field_name"))
            compatible = compatible and {"denseVector", "sparseVector"}.issubset(indexed)
            if not compatible:
                raise AppError("MILVUS_SCHEMA_CONFLICT", "已有向量集合结构不兼容，未覆盖", 503)
            return
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunkId", DataType.VARCHAR, is_primary=True, max_length=24)
        schema.add_field("documentId", DataType.VARCHAR, max_length=24)
        schema.add_field("denseVector", DataType.FLOAT_VECTOR, dim=1024)
        schema.add_field("sparseVector", DataType.SPARSE_FLOAT_VECTOR)
        indexes = self.client.prepare_index_params()
        indexes.add_index("denseVector", index_type="FLAT", metric_type="IP")
        indexes.add_index("sparseVector", index_type="SPARSE_INVERTED_INDEX", metric_type="IP")
        self.client.create_collection(
            self.collection, schema=schema, index_params=indexes, consistency_level="Strong"
        )
        logger.info("Milvus collection created collection=%s", self.collection)

    def upsert(self, rows: list[dict]):
        for start in range(0, len(rows), 32):
            self.client.upsert(self.collection, rows[start : start + 32])
        logger.info("vectors written count=%s", len(rows))

    def delete(self, document_id: str):
        # Only verified ObjectIds can enter the expression; never interpolate input.
        from bson import ObjectId

        safe = str(ObjectId(document_id))
        self.client.delete(self.collection, filter=f'documentId == "{safe}"')

    def search(self, dense, sparse, document_ids: list[str], limit: int = 6) -> list[str]:
        from bson import ObjectId
        from pymilvus import AnnSearchRequest, RRFRanker

        ids = [str(ObjectId(value)) for value in document_ids]
        if not ids:
            return []
        expression = "documentId in [" + ",".join(f'"{value}"' for value in ids) + "]"
        requests = [
            AnnSearchRequest(
                [dense], "denseVector", {"metric_type": "IP"}, limit * 3, expr=expression
            ),
            AnnSearchRequest(
                [sparse], "sparseVector", {"metric_type": "IP"}, limit * 3, expr=expression
            ),
        ]
        result = self.client.hybrid_search(
            self.collection,
            requests,
            RRFRanker(),
            limit=limit,
            output_fields=["chunkId"],
            consistency_level="Strong",
        )
        # Milvus 2.5 returns the declared primary-field name, not a synthetic "id".
        return [str(hit["chunkId"]) for hit in result[0]]

    def close(self):
        self.client.close()


class DeepSeekModel:
    """Provider-native streaming; failure is propagated, with no mock fallback."""

    def __init__(self, settings):
        from openai import AsyncOpenAI

        self.model = settings.deepseek_model
        self.max_tokens = getattr(settings, "generation_max_tokens", 1600)
        self.client = AsyncOpenAI(
            api_key=settings.deepseek_api_key.get_secret_value() or "not-configured",
            base_url=settings.deepseek_base_url,
            max_retries=0,
            timeout=120,
        )

    async def stream(self, messages):
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=self.max_tokens,
            temperature=0.2,
        )
        stopped = False
        try:
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                if chunk.choices and chunk.choices[0].finish_reason:
                    reason = chunk.choices[0].finish_reason
                    if reason != "stop":
                        raise AppError(
                            "MODEL_OUTPUT_INCOMPLETE", "模型回答未完整结束，已保留部分内容", 502
                        )
                    stopped = True
            if not stopped:
                raise AppError("MODEL_STREAM_INTERRUPTED", "模型连接中断，回答未完整结束", 502)
        finally:
            await stream.close()

    async def close(self):
        await self.client.close()
