import pytest
from pydantic import ValidationError

from medical_consultation_backend.core.config import Settings


def test_heartbeat_must_leave_renewal_margin() -> None:
    with pytest.raises(ValidationError):
        Settings(worker_heartbeat_seconds=30, worker_lease_seconds=60)


def test_secrets_are_hidden_from_settings_repr() -> None:
    settings = Settings(deepseek_api_key="private-key", mongo_uri="mongodb://secret-host")
    assert "private-key" not in repr(settings)
    assert "secret-host" not in repr(settings)
