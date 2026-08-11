import argparse
from typing import Any

from aiohttp import web

IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?"
    b"\x00\x05\xfe\x02\xfeA\xe2p\xb9\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeYandex:
    def __init__(self) -> None:
        self._requests: list[dict[str, Any]] = []

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_requests(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": self._requests})

    async def reset(self, request: web.Request) -> web.Response:
        self._requests.clear()
        return web.json_response({"ok": True})

    async def resources(self, request: web.Request) -> web.Response:
        params = dict(request.query)
        self._requests.append({"method": "resources", "params": params})

        directory = params.get("path", "app:/functional").removeprefix("app:/").strip("/")
        if directory == "empty":
            items: list[dict[str, str]] = []
        else:
            items = [
                {
                    "name": "image.png",
                    "mime_type": "image/png",
                    "path": f"disk:/Приложения/cringe-pics-telebot/{directory}/image.png",
                }
            ]

        return web.json_response({"_embedded": {"items": items}})

    async def download_resource(self, request: web.Request) -> web.Response:
        params = dict(request.query)
        self._requests.append({"method": "resources/download", "params": params})

        return web.json_response({"href": f"{request.scheme}://{request.host}/download/image.png"})

    async def download_file(self, request: web.Request) -> web.Response:
        self._requests.append({"method": "download", "path": request.match_info["path"]})
        return web.Response(body=IMAGE_BYTES, content_type="image/png")


def create_app() -> web.Application:
    fake = FakeYandex()
    app = web.Application()
    app.router.add_get("/healthz", fake.healthcheck)
    app.router.add_get("/test/requests", fake.list_requests)
    app.router.add_post("/test/reset", fake.reset)
    app.router.add_get("/v1/disk/resources", fake.resources)
    app.router.add_get("/v1/disk/resources/download", fake.download_resource)
    app.router.add_get("/download/{path:.*}", fake.download_file)
    return app


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    web.run_app(create_app(), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
