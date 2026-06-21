import hashlib
import json
import logging
from collections.abc import Callable, Coroutine
from datetime import timedelta
from typing import Any, Protocol, get_type_hints, overload, runtime_checkable

from cringe_pics_telebot.helpers.serializers import get_serializer

from .connection import get_connection

logger = logging.getLogger(__name__)


@runtime_checkable
class _CallableWithName[**P, R](Protocol):
    __name__: str

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R: ...


class _CachedWrapper[**P, R](Protocol):
    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R | None: ...
    async def recache(self, *args: P.args, **kwargs: P.kwargs) -> R: ...


type _Wrappable[**P, R] = _CallableWithName[P, Coroutine[Any, Any, R]]


async def set[T](*, key: str, value: T, cls: type[T], ttl: timedelta | None = None) -> None:
    async with get_connection() as conn:
        serializer = get_serializer(cls)

        try:
            await conn.set(name=key, value=json.dumps(serializer.dump(value)), ex=ttl)
        except Exception:
            logger.exception("Tried to serialize %s", value)
            raise


async def get[T](*, key: str, cls: type[T]) -> T | None:
    async with get_connection() as conn:
        value = await conn.get(name=key)
        if value is None:
            return None

        serializer = get_serializer(cls)
        return serializer.load(json.loads(value))


def _make_key[**P, R](func: _Wrappable[P, R], *args: P.args, **kwargs: P.kwargs) -> str:
    try:
        payload = json.dumps((args, kwargs), default=repr, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        payload = repr((args, kwargs))

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{func.__module__}.{func.__name__}:{digest}"


@overload
def cached[**P, R](func: _Wrappable[P, R], *, ttl: None = None) -> _CachedWrapper[P, R]: ...


@overload
def cached[**P, R](
    func: None = None, *, ttl: timedelta | None = None
) -> Callable[[_Wrappable[P, R]], _CachedWrapper[P, R]]: ...


def cached[**P, R](
    func: _Wrappable[P, R] | None = None,
    *,
    ttl: timedelta | None = None,
) -> Callable[[_Wrappable[P, R]], _CachedWrapper[P, R]] | _CachedWrapper[P, R]:
    def decorator(func: _Wrappable[P, R]) -> _CachedWrapper[P, R]:
        return Cached(func=func, ttl=ttl)

    if func is not None:
        return decorator(func)

    return decorator


class Cached[**P, R]:
    def __init__(self, *, func: _Wrappable[P, R], ttl: timedelta | None = None) -> None:
        self.func = func
        self.ttl = ttl

        self._func_return_type = get_type_hints(func).get("return")

    async def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R | None:
        key = _make_key(self.func, *args, **kwargs)
        if self._func_return_type and (cached_value := await get(key=key, cls=self._func_return_type)) is not None:
            return cached_value

        result = await self.func(*args, **kwargs)
        await set(key=key, value=result, cls=self._func_return_type, ttl=self.ttl)  # type: ignore[arg-type]

        return result

    async def recache(self, *args: P.args, **kwargs: P.kwargs) -> R:
        key = _make_key(self.func, *args, **kwargs)
        result = await self.func(*args, **kwargs)
        await set(key=key, value=result, cls=self._func_return_type, ttl=self.ttl)  # type: ignore[arg-type]
        return result
