import argparse
import asyncio
import json
from typing import Any

from aiohttp import web


class FakeOllama:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._requests: list[dict[str, Any]] = []
        self._responses: list[dict[str, Any]] = []
        self._release = asyncio.Event()
        self._release.set()

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def reset(self, request: web.Request) -> web.Response:
        self._release.set()
        self._release = asyncio.Event()
        self._release.set()
        async with self._condition:
            self._requests.clear()
            self._responses.clear()
        return web.json_response({"ok": True})

    async def configure(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self._responses = list(payload.get("responses", []))
        if payload.get("block", False):
            self._release.clear()
        else:
            self._release.set()
        return web.json_response({"ok": True})

    async def release(self, request: web.Request) -> web.Response:
        self._release.set()
        return web.json_response({"ok": True})

    async def requests(self, request: web.Request) -> web.Response:
        return web.json_response({"result": self._requests})

    async def wait_for_requests(self, request: web.Request) -> web.Response:
        count = int(request.query.get("count", "1"))
        async with asyncio.timeout(10), self._condition:
            await self._condition.wait_for(lambda: len(self._requests) >= count)
        return web.json_response({"result": self._requests})

    async def chat(self, request: web.Request) -> web.Response:
        payload = await request.json()
        release = self._release
        async with self._condition:
            index = len(self._requests)
            self._requests.append({"payload": payload, "authorization": request.headers.get("Authorization")})
            response = self._responses[min(index, len(self._responses) - 1)] if self._responses else {}
            self._condition.notify_all()
        await release.wait()
        if "body" in response:
            return web.Response(text=response["body"], status=response.get("status", 200))
        if response.get("status", 200) != 200:
            return web.json_response({"error": "Functional Ollama failure"}, status=response["status"])
        content = response.get("content", json.dumps({"aliases": ["  Сонный кот  ", "СОННЫЙ   КОТ", "Кофе"]}))
        return web.json_response({"message": {"role": "assistant", "content": content}, "done": True})


def create_app() -> web.Application:
    fake = FakeOllama()
    app = web.Application()
    app.router.add_get("/healthz", fake.healthcheck)
    app.router.add_get("/test/requests", fake.requests)
    app.router.add_get("/test/wait", fake.wait_for_requests)
    app.router.add_post("/test/reset", fake.reset)
    app.router.add_post("/test/configure", fake.configure)
    app.router.add_post("/test/release", fake.release)
    app.router.add_post("/api/chat", fake.chat)
    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    web.run_app(create_app(), host="127.0.0.1", port=args.port, print=None)


if __name__ == "__main__":
    main()
