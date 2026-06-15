import logging

from serpyco_rs import Serializer

_SERIALIZERS: dict[type, Serializer] = {}

logger = logging.getLogger(__name__)


def make_serializer[T](cls: type[T]) -> type[T]:
    if cls not in _SERIALIZERS:
        _SERIALIZERS[cls] = Serializer(cls)

    return cls


def get_serializer[T](cls: type[T]) -> Serializer[T]:
    if cls not in _SERIALIZERS:
        logger.warning(f"Serializer for {cls} not found, creating a new one")
        make_serializer(cls)

    return _SERIALIZERS[cls]
