"""Application logging configuration and logger factory."""

from __future__ import annotations

import logging
import os
import sys
from copy import copy
from typing import TextIO

from dotenv import load_dotenv

load_dotenv()

LOGGER_NAME = "medical_consultation"
_HANDLER_MARKER = "_medical_consultation_handler"
_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_LEVEL_COLORS = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
_COLOR_RESET = "\033[0m"


class _LevelColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        colored_record = copy(record)
        color = _LEVEL_COLORS.get(colored_record.levelno)
        if color:
            colored_record.levelname = f"{color}{colored_record.levelname}{_COLOR_RESET}"
        return super().format(colored_record)


def _resolve_level(level: str | int | None) -> int:
    configured_level = level if level is not None else os.getenv("LOG_LEVEL", "DEBUG")
    if isinstance(configured_level, int):
        if configured_level < 0:
            raise ValueError("日志级别必须为非负整数或有效级别名称")
        return configured_level

    resolved_level = logging.getLevelName(configured_level.strip().upper())
    if not isinstance(resolved_level, int):
        raise ValueError(f"无效的日志级别: {configured_level!r}")
    return resolved_level


def _resolve_color(color: bool | None, stream: TextIO) -> bool:
    if color is not None:
        return color

    mode = os.getenv("LOG_COLOR", "auto").strip().lower()
    if mode not in {"auto", "always", "never"}:
        raise ValueError("LOG_COLOR 必须为 auto、always 或 never")
    if mode == "always":
        return True
    if mode == "never" or os.getenv("NO_COLOR") is not None:
        return False

    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty())


def setup_logging(
    level: str | int | None = None,
    *,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> None:
    """Configure the app logger; level defaults to the LOG_LEVEL environment variable."""
    resolved_level = _resolve_level(level)
    output_stream = stream or sys.stderr
    use_color = _resolve_color(color, output_stream)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(resolved_level)
    logger.propagate = False

    handler = next(
        (item for item in logger.handlers if getattr(item, _HANDLER_MARKER, False)),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler(output_stream)
        setattr(handler, _HANDLER_MARKER, True)
        logger.addHandler(handler)
    elif stream is not None:
        handler.setStream(stream)

    handler.setLevel(resolved_level)
    formatter_type = _LevelColorFormatter if use_color else logging.Formatter
    handler.setFormatter(formatter_type(_FORMAT, datefmt=_DATE_FORMAT))


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a named logger under the application namespace."""
    if not name or name == LOGGER_NAME:
        logger_name = LOGGER_NAME
    elif name.startswith(f"{LOGGER_NAME}."):
        logger_name = name
    else:
        logger_name = f"{LOGGER_NAME}.{name}"
    return logging.getLogger(logger_name)


if __name__ == "__main__":
    setup_logging()
    logger = get_logger()
    logger.info("Hello, world!")
    logger.warning("This is a warning message.")
    logger.error("This is an error message.")
