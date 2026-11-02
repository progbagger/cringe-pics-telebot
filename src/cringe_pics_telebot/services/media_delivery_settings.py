import os
from collections.abc import Mapping

DEFAULT_MEDIA_DELIVERY_MAX_RETRIES = 3


def load_media_delivery_max_retries(environ: Mapping[str, str] | None = None) -> int:
    environ = os.environ if environ is None else environ
    try:
        value = int(environ.get("MEDIA_DELIVERY_MAX_RETRIES", str(DEFAULT_MEDIA_DELIVERY_MAX_RETRIES)))
        if value < 0:
            raise ValueError
    except ValueError:
        raise ValueError("MEDIA_DELIVERY_MAX_RETRIES must be a non-negative integer") from None

    return value
