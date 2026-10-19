import logging
from io import StringIO

import pytest

from app.core.loggingUtils import LOGGER_NAME, get_logger, setup_logging


@pytest.fixture(autouse=True)
def reset_application_logger():
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.setLevel(logging.NOTSET)
    logger.propagate = True
    yield
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_get_logger_uses_application_namespace() -> None:
    assert get_logger("app.modules.patient.service").name == (
        f"{LOGGER_NAME}.app.modules.patient.service"
    )
    assert get_logger(f"{LOGGER_NAME}.worker").name == f"{LOGGER_NAME}.worker"


def test_setup_logging_formats_records_and_applies_level() -> None:
    output = StringIO()
    setup_logging("WARNING", stream=output)
    logger = get_logger("worker")

    logger.info("hidden")
    logger.warning("task failed")

    assert "hidden" not in output.getvalue()
    assert "| WARNING | medical_consultation.worker | task failed" in output.getvalue()


def test_setup_logging_is_idempotent() -> None:
    output = StringIO()
    setup_logging("INFO", stream=output)
    setup_logging("DEBUG")
    get_logger("api").info("once")

    assert output.getvalue().count("once") == 1
    assert logging.getLogger(LOGGER_NAME).level == logging.DEBUG


def test_setup_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="无效的日志级别"):
        setup_logging("verbose")


def test_setup_logging_colors_level_when_enabled() -> None:
    output = StringIO()
    setup_logging("INFO", stream=output, color=True)
    get_logger("api").error("request failed")

    assert "\033[31mERROR\033[0m" in output.getvalue()
    assert "request failed" in output.getvalue()


def test_setup_logging_keeps_redirected_output_plain_by_default(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("LOG_COLOR", "auto")
    setup_logging("INFO", stream=output)
    get_logger("api").info("request complete")

    assert "\033[" not in output.getvalue()


def test_setup_logging_rejects_unknown_color_mode(monkeypatch) -> None:
    monkeypatch.setenv("LOG_COLOR", "sometimes")
    with pytest.raises(ValueError, match="LOG_COLOR"):
        setup_logging("INFO", stream=StringIO())
