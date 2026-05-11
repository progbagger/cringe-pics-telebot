import logging
from collections.abc import Callable, Coroutine, Iterable
from functools import wraps
from typing import Any

from aiogram.types import CallbackQuery, Message

type _Coro[T] = Coroutine[Any, Any, T]

type _Handler[T] = Callable[[T], _Coro[None]]

type _UsableInHandler = Message | CallbackQuery

_logger = logging.getLogger(__name__)


class AbsentAttributesError(AttributeError):
    absent_attributes: Iterable[str]

    def __init__(self, attributes: Iterable[str]) -> None:
        self.absent_attributes = attributes
        super().__init__(attributes)


def check_has_properties[T: _UsableInHandler](
    *properties: str,
    logger: logging.Logger | None = _logger,
    _raise: bool = False,
) -> Callable[[_Handler[T]], _Handler[T]]:
    def decorator(f: _Handler[T]) -> _Handler[T]:
        @wraps(f)
        async def wrapper(obj: T) -> None:
            absent_properties: list[str] = []
            for property in properties:
                if not hasattr(obj, property):
                    absent_properties.append(property)

            if absent_properties:
                if logger is not None:
                    logger.error(
                        "No necessary attributes in passed telegram object",
                        extra={
                            "function_name": f.__name__,
                            "checked_properties": properties,
                            "absent_properties": absent_properties,
                        },
                    )

                if _raise:
                    raise AbsentAttributesError(absent_properties)

                return

            await f(obj)

        return wrapper

    return decorator
