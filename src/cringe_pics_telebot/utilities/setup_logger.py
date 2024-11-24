import logging
import sys
from enum import IntEnum
from typing import TextIO


class LoggingLevel(IntEnum):
    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL


def setup_logger(
    *,
    name: str,
    level: LoggingLevel | int | None = None,
    file: TextIO | str | None = None,
    fmt: str | None = None,
) -> logging.Logger:
    if not level:
        level = LoggingLevel(logging.WARNING)
    if not file:
        file = sys.stderr
    if not fmt:
        fmt = "%(asctime)s - %(levelname)s - %(message)s"

    logger = logging.getLogger(name)
    logger.setLevel(int(level) if isinstance(level, LoggingLevel) else level)
    logger.handlers = []

    formatter = logging.Formatter(fmt=fmt)

    if isinstance(file, str):
        handler: logging.StreamHandler = logging.FileHandler(file)
    else:
        handler = logging.StreamHandler(file)

    handler.setFormatter(formatter)
    handler.setLevel(level)

    logger.addHandler(handler)

    return logger
