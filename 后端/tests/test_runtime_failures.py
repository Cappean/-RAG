"""Provider termination and shutdown failures must not become false success."""

from types import SimpleNamespace

import pytest

from app.core.errors import AppError
from app.infrastructure.runtime import DeepSeekModel
from app.infrastructure.services import close


class ProviderStream:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason
        self.closed = False

    async def __aiter__(self):
        yield SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="partial"), finish_reason=None)]
        )
        if self.finish_reason:
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None), finish_reason=self.finish_reason
                    )
                ]
            )

    async def close(self):
        self.closed = True


@pytest.mark.parametrize("reason", ["length", "content_filter", None])
async def test_provider_incomplete_termination_is_failure(reason):
    stream = ProviderStream(reason)

    async def create(**kwargs):
        return stream

    model = DeepSeekModel.__new__(DeepSeekModel)
    model.model, model.max_tokens = "test", 100
    model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    partial = []
    with pytest.raises(AppError):
        async for text in model.stream([]):
            partial.append(text)
    assert partial == ["partial"] and stream.closed


async def test_cleanup_attempts_every_resource_after_one_close_fails():
    attempts = []

    async def close_model():
        attempts.append("model")
        raise OSError("provider unavailable")

    def close_vectors():
        attempts.append("vectors")
        raise OSError("vector unavailable")

    async def close_mongo():
        attempts.append("mongo")

    await close(
        (
            SimpleNamespace(close=close_mongo),
            SimpleNamespace(close=close_vectors),
            SimpleNamespace(close=close_model),
        )
    )
    assert attempts == ["model", "vectors", "mongo"]
