from logging import Logger

from aiohttp import (
    ClientHandlerType,
    ClientMiddlewareType,
    ClientRequest,
    ClientResponse,
)


def log_request(*, logger: Logger, method: str, url: str, status: int) -> None:
    f = logger.info if status < 400 else logger.error
    f("%s %d %s", method, status, url)


def aiohttp_logging_middleware_factory(logger: Logger) -> ClientMiddlewareType:
    async def log_middleware(
        request: ClientRequest, handler: ClientHandlerType
    ) -> ClientResponse:
        response = await handler(request)

        log_request(
            logger=logger,
            method=response.method,
            url=response.url.human_repr(),
            status=response.status,
        )

        return response

    return log_middleware
