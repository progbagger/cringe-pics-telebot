import argparse
import asyncio
import json
import time
from typing import Any

from aiohttp import web


class FakeTelegram:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._updates: list[dict[str, Any]] = []
        self._requests: list[dict[str, Any]] = []
        self._blocked_methods: set[str] = set()
        self._forbidden_chat_ids: set[int] = set()
        self._invalid_file_ids: set[str] = set()
        self._next_update_id = 1
        self._next_message_id = 1

    async def healthcheck(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def push_update(self, request: web.Request) -> web.Response:
        payload = await request.json()
        update_id = int(payload.pop("update_id", self._next_update_id))
        self._next_update_id = max(self._next_update_id, update_id + 1)
        update = {"update_id": update_id, **payload}

        async with self._condition:
            self._updates.append(update)
            self._condition.notify_all()

        return web.json_response({"ok": True, "result": update})

    async def list_requests(self, request: web.Request) -> web.Response:
        method = request.query.get("method")
        requests = self._requests
        if method is not None:
            requests = [entry for entry in requests if entry["method"] == method]

        return web.json_response({"ok": True, "result": requests})

    async def reset(self, request: web.Request) -> web.Response:
        async with self._condition:
            self._updates.clear()
            self._requests.clear()
            self._blocked_methods.clear()
            self._forbidden_chat_ids.clear()
            self._invalid_file_ids.clear()
            self._condition.notify_all()

        return web.json_response({"ok": True})

    async def block_method(self, request: web.Request) -> web.Response:
        payload = await request.json()
        async with self._condition:
            self._blocked_methods.add(str(payload["method"]))
        return web.json_response({"ok": True})

    async def release_method(self, request: web.Request) -> web.Response:
        payload = await request.json()
        async with self._condition:
            self._blocked_methods.discard(str(payload["method"]))
            self._condition.notify_all()
        return web.json_response({"ok": True})

    async def set_forbidden_chats(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self._forbidden_chat_ids = {int(chat_id) for chat_id in payload.get("chat_ids", [])}
        return web.json_response({"ok": True})

    async def set_invalid_file_ids(self, request: web.Request) -> web.Response:
        payload = await request.json()
        self._invalid_file_ids = {str(file_id) for file_id in payload.get("file_ids", [])}
        return web.json_response({"ok": True})

    async def handle_bot_api(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = await self._read_payload(request)
        async with self._condition:
            self._requests.append(
                {
                    "method": method,
                    "token": request.match_info["bot_token"].removeprefix("bot"),
                    "payload": payload,
                }
            )
            while method in self._blocked_methods:
                await self._condition.wait()

        chat_id = int(payload.get("chat_id") or 0)
        if method == "copyMessage" and chat_id in self._forbidden_chat_ids:
            return web.json_response(
                {
                    "ok": False,
                    "error_code": 403,
                    "description": "Forbidden: bot was blocked by the user",
                },
                status=403,
            )
        if _request_media_id(method, payload) in self._invalid_file_ids:
            return web.json_response(
                {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: wrong file identifier/HTTP URL specified",
                },
                status=400,
            )

        match method:
            case "getMe":
                result: Any = {
                    "id": 123456,
                    "is_bot": True,
                    "first_name": "Functional Test Bot",
                    "username": "functional_test_bot",
                }
            case "deleteWebhook":
                result = True
            case "getUpdates":
                result = await self._get_updates(payload)
            case "sendMessage":
                result = self._message_from_payload(payload)
            case "sendPhoto":
                result = self._sent_media_message_from_payload(payload, media_key="photo")
            case "sendAnimation":
                result = self._sent_media_message_from_payload(payload, media_key="animation")
            case "copyMessage":
                self._next_message_id += 1
                result = {"message_id": self._next_message_id}
            case "editMessageText" | "editMessageReplyMarkup":
                result = self._message_from_payload(payload)
            case "answerCallbackQuery":
                result = True
            case "answerInlineQuery":
                result = True
            case "editMessageMedia":
                result = self._media_message_from_payload(payload)
            case _:
                result = True

        return web.json_response({"ok": True, "result": result})

    async def _get_updates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        offset = int(payload.get("offset") or 0)
        timeout = min(float(payload.get("timeout") or 0), 10)
        deadline = asyncio.get_running_loop().time() + timeout

        async with self._condition:
            while True:
                self._updates = [update for update in self._updates if update["update_id"] >= offset]
                if self._updates:
                    updates = list(self._updates)
                    self._updates.clear()
                    return updates

                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    return []

                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    return []

    def _message_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        self._next_message_id += 1
        chat_id = int(payload.get("chat_id") or payload.get("chat", {}).get("id") or 1)
        text = payload.get("text") or ""
        message = {
            "message_id": self._next_message_id,
            "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        }
        reply_markup = payload.get("reply_markup")
        if isinstance(reply_markup, dict) and "inline_keyboard" in reply_markup:
            message["reply_markup"] = reply_markup

        return message

    def _media_message_from_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        message = self._message_from_payload(payload)
        media = payload.get("media")
        media_id = "functional-media-file-id"
        if isinstance(media, dict):
            media_id = media.get("media", media_id)

        if isinstance(media, dict) and media.get("type") == "animation":
            message["animation"] = {
                "file_id": "functional-animation-file-id",
                "file_unique_id": "functional-animation-file-unique-id",
                "width": 1,
                "height": 1,
                "duration": 1,
            }
        else:
            message["photo"] = [
                {
                    "file_id": "functional-photo-file-id" if _is_uploaded_media(media_id) else str(media_id),
                    "file_unique_id": "functional-photo-file-unique-id",
                    "width": 1,
                    "height": 1,
                }
            ]
        return message

    def _sent_media_message_from_payload(self, payload: dict[str, Any], *, media_key: str) -> dict[str, Any]:
        message = self._message_from_payload(payload)
        media_id = str(payload.get(media_key) or f"functional-{media_key}-file-id")

        if media_key == "animation":
            message["animation"] = {
                "file_id": "functional-animation-file-id" if _is_uploaded_media(media_id) else media_id,
                "file_unique_id": "functional-animation-file-unique-id",
                "width": 1,
                "height": 1,
                "duration": 1,
            }
        else:
            message["photo"] = [
                {
                    "file_id": "functional-photo-file-id" if _is_uploaded_media(media_id) else media_id,
                    "file_unique_id": "functional-photo-file-unique-id",
                    "width": 1,
                    "height": 1,
                }
            ]

        return message

    async def _read_payload(self, request: web.Request) -> dict[str, Any]:
        if request.can_read_body and request.content_type == "application/json":
            return await request.json()

        if request.can_read_body:
            form = await request.post()
            return {key: self._decode_form_value(value) for key, value in form.items()}

        return dict(request.query)

    def _decode_form_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return str(value)

        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


def _is_uploaded_media(media: object) -> bool:
    media_id = str(media)
    return media_id.startswith(("attach://", "http://", "https://"))


def _request_media_id(method: str, payload: dict[str, Any]) -> str | None:
    if method == "sendPhoto":
        return str(payload.get("photo"))
    if method == "sendAnimation":
        return str(payload.get("animation"))
    if method == "editMessageMedia" and isinstance(payload.get("media"), dict):
        return str(payload["media"].get("media"))
    return None


def create_app() -> web.Application:
    fake = FakeTelegram()
    app = web.Application()
    app.router.add_get("/healthz", fake.healthcheck)
    app.router.add_post("/test/updates", fake.push_update)
    app.router.add_get("/test/requests", fake.list_requests)
    app.router.add_post("/test/reset", fake.reset)
    app.router.add_post("/test/block-method", fake.block_method)
    app.router.add_post("/test/release-method", fake.release_method)
    app.router.add_post("/test/forbidden-chats", fake.set_forbidden_chats)
    app.router.add_post("/test/invalid-file-ids", fake.set_invalid_file_ids)
    app.router.add_route("*", "/{bot_token}/{method}", fake.handle_bot_api)
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
