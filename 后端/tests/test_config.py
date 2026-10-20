import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_queue_capacity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, worker_queue_capacity=0)


def test_secrets_are_hidden_from_settings_repr() -> None:
    settings = Settings(
        _env_file=None, deepseek_api_key="private-key", mongo_uri="mongodb://secret-host"
    )
    assert "private-key" not in repr(settings)
    assert "secret-host" not in repr(settings)


def test_legacy_and_prefixed_env_keys_are_supported(monkeypatch) -> None:
    monkeypatch.setenv("MONGO_DATABASE", "legacy")
    monkeypatch.setenv("MEDICAL_MONGO_DATABASE", "prefixed")
    assert Settings(_env_file=None).mongo_database == "legacy"
    monkeypatch.delenv("MONGO_DATABASE")
    assert Settings(_env_file=None).mongo_database == "prefixed"


def test_embedding_is_cpu_only() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, embedding_device="cuda")
