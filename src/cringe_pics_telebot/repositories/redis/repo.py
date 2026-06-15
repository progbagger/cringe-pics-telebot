import json
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, get_type_hints, overload

from cringe_pics_telebot.helpers.serializers import get_serializer

from .connection import get_connection

type _Wrappable[**P, R] = Callable[P, Coroutine[Any, Any, R]]


async def set(*, key: str, value: Any, ttl: timedelta | None = None) -> None:
    async with get_connection() as conn:
        serializer = get_serializer(type(value))
        await conn.set(name=key, value=json.dumps(serializer.dump(value)), ex=ttl)


async def get[T](*, key: str, cls: type[T]) -> T | None:
    async with get_connection() as conn:
        value = await conn.get(name=key)
        if value is None:
            return None

        serializer = get_serializer(cls)
        return serializer.load(json.loads(value))


@overload
def cached[**P, R](func: _Wrappable[P, R]) -> _Wrappable[P, R]: ...


@overload
def cached[**P, R](*, ttl: timedelta) -> Callable[[_Wrappable[P, R]], _Wrappable[P, R]]: ...


def _make_key[**P, R](func: _Wrappable[P, R], *args: P.args, **kwargs: P.kwargs) -> str:
    return f"{id(func)}:{args}:{kwargs}"


def cached[**P, R](
    func: _Wrappable[P, R] | None = None,
    *,
    ttl: timedelta | None = None,
) -> Callable[[_Wrappable[P, R]], _Wrappable[P, R]] | _Wrappable[P, R]:
    def decorator(func: _Wrappable[P, R]) -> _Wrappable[P, R]:
        hints = get_type_hints(func)
        return_type = hints.get("return")

        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = _make_key(func, *args, **kwargs)
            if return_type and (cached_value := await get(key=key, cls=return_type)) is not None:
                return cached_value

            result = await func(*args, **kwargs)
            await set(key=key, value=result, ttl=ttl)
            return result

        return wrapper

    if func is not None:
        return decorator(func)

    return decorator
