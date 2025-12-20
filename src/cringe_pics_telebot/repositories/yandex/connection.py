from .yandex import YandexS3Client

_client: YandexS3Client | None = None


class S3ConnectionError(ConnectionError): ...


class NotConnectedError(S3ConnectionError): ...


class AlreadyConnectedError(S3ConnectionError): ...


def connect(token: str) -> None:
    global _client
    if _client is not None:
        raise AlreadyConnectedError

    _client = YandexS3Client(token)


def get_connection() -> YandexS3Client:
    if _client is None:
        raise NotConnectedError

    return _client
