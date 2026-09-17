import argparse
import asyncio
import base64
from datetime import UTC, datetime
from hashlib import sha256
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
        self._directory_overrides: dict[str, list[dict[str, Any]]] = {}
        self._failed_directories: set[str] = set()
        self._downloads: dict[str, dict[str, Any]] = {}
        self._download_barriers: dict[str, asyncio.Event] = {}
        self._download_condition = asyncio.Condition()

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def list_requests(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "result": self._requests})

    async def reset(self, request: web.Request) -> web.Response:
        for barrier in self._download_barriers.values():
            barrier.set()
        self._download_barriers.clear()
        self._requests.clear()
        self._directory_overrides.clear()
        self._failed_directories.clear()
        self._downloads.clear()
        return web.json_response({"ok": True})

    async def configure_directory(self, request: web.Request) -> web.Response:
        payload = await request.json()
        directory = str(payload["directory"]).strip("/")
        if payload.get("fail", False):
            self._failed_directories.add(directory)
            self._directory_overrides.pop(directory, None)
        else:
            self._failed_directories.discard(directory)
            self._directory_overrides[directory] = list(payload.get("images", []))
        return web.json_response({"ok": True})

    async def configure_download(self, request: web.Request) -> web.Response:
        payload = await request.json()
        name = str(payload["name"])
        self._downloads[name] = payload
        barrier = self._download_barriers.setdefault(name, asyncio.Event())
        if payload.get("block", False):
            barrier.clear()
        else:
            barrier.set()
        return web.json_response({"ok": True})

    async def release_download(self, request: web.Request) -> web.Response:
        name = (await request.json())["name"]
        self._download_barriers[name].set()
        return web.json_response({"ok": True})

    async def wait_for_download(self, request: web.Request) -> web.Response:
        name = request.query["name"]
        async with asyncio.timeout(10), self._download_condition:
            await self._download_condition.wait_for(
                lambda: any(item["method"] == "download" and item["path"] == name for item in self._requests)
            )
        return web.json_response({"ok": True})

    async def resources(self, request: web.Request) -> web.Response:
        params = dict(request.query)
        self._requests.append({"method": "resources", "params": params})

        directory = params.get("path", "app:/functional").removeprefix("app:/").strip("/")
        if directory in self._failed_directories:
            raise web.HTTPServiceUnavailable(text="Functional Yandex listing failure")
        if directory in self._directory_overrides:
            items = [self._resource(directory, image) for image in self._directory_overrides[directory]]
        elif directory == "empty":
            items = []
        else:
            image_names = ["image.png", "second.png"] if directory == "day" else ["image.png"]
            items = [self._resource(directory, {"name": image_name}) for image_name in image_names]

        return web.json_response({"_embedded": {"items": items}})

    @staticmethod
    def _resource(directory: str, image: dict[str, Any]) -> dict[str, Any]:
        image_name = str(image["name"])
        return {
            "name": image_name,
            "mime_type": image.get("mime_type", "image/png"),
            "path": f"disk:/Приложения/cringe-pics-telebot/{directory}/{image_name}",
            "sha256": image.get("sha256", sha256(f"{directory}/{image_name}".encode()).hexdigest()),
            "size": image.get("size", len(IMAGE_BYTES)),
            "modified": image.get("modified", datetime(2026, 8, 19, tzinfo=UTC).isoformat()),
        }

    async def download_resource(self, request: web.Request) -> web.Response:
        params = dict(request.query)
        self._requests.append({"method": "resources/download", "params": params})

        if "broken" in params.get("path", ""):
            raise web.HTTPServiceUnavailable(text="Functional Yandex failure")

        image_name = params.get("path", "image.png").rsplit("/", maxsplit=1)[-1]
        return web.json_response({"href": f"{request.scheme}://{request.host}/download/{image_name}"})

    async def download_file(self, request: web.Request) -> web.Response:
        name = request.match_info["path"]
        self._requests.append(
            {
                "method": "download",
                "path": name,
                "authorization": request.headers.get("Authorization"),
            }
        )
        async with self._download_condition:
            self._download_condition.notify_all()
        configured = self._downloads.get(name, {})
        if barrier := self._download_barriers.get(name):
            await barrier.wait()
        body = base64.b64decode(configured["content"]) if "content" in configured else IMAGE_BYTES
        return web.Response(
            body=body,
            content_type=configured.get("content_type", "image/png"),
            status=configured.get("status", 200),
        )


def create_app() -> web.Application:
    fake = FakeYandex()
    app = web.Application()
    app.router.add_get("/healthz", fake.healthcheck)
    app.router.add_get("/test/requests", fake.list_requests)
    app.router.add_post("/test/reset", fake.reset)
    app.router.add_post("/test/directory", fake.configure_directory)
    app.router.add_post("/test/download", fake.configure_download)
    app.router.add_post("/test/release-download", fake.release_download)
    app.router.add_get("/test/wait-download", fake.wait_for_download)
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
