from asyncio import subprocess
from typing import Any

from tests.functional.conftest import FakeTelegramServer


async def test_bot_answers_start_command(
    bot_process: subprocess.Process,
    fake_telegram_server: FakeTelegramServer,
) -> None:
    await fake_telegram_server.push_message(text="/start", first_name="Danil")

    request = await fake_telegram_server.wait_for_request(
        "sendMessage",
        predicate=_is_start_answer,
    )

    payload = request["payload"]
    assert payload["chat_id"] == 42
    assert "Приветствую" in payload["text"]
    assert "Danil" in payload["text"]
    assert payload["reply_markup"]["keyboard"][0][0]["text"] == "Подписки"


def _is_start_answer(request: dict[str, Any]) -> bool:
    payload = request["payload"]
    return payload.get("chat_id") == 42 and "Приветствую" in payload.get("text", "")
