import logging
from enum import IntEnum
import sys
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
    level: LoggingLevel | None = None,
    file: TextIO | str | None = None,
    fmt: str | None = None,
) -> logging.Logger:
    if not level:
        level = logging.WARNING
    if not file:
        file = sys.stderr
    if not fmt:
        fmt = "%(asctime)s - %(levelname)s - %(message)s"

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers = []

    formatter = logging.Formatter(fmt=fmt)

    if isinstance(file, str):
        handler_cls = logging.FileHandler
    else:
        handler_cls = logging.StreamHandler

    handler = handler_cls(file)
    handler.setFormatter(formatter)
    handler.setLevel(level)

    logger.addHandler(handler)

    return logger
