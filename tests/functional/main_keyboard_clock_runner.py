"""Run the real bot with an in-memory, externally controlled keyboard clock."""

import argparse
import asyncio
from datetime import UTC, datetime

from aiohttp import web

from cringe_pics_telebot.cli import start_polling


async def run(port: int, wall_now: datetime | None = None) -> None:
    now = 0.0

    async def advance(request: web.Request) -> web.Response:
        nonlocal now, wall_now
        payload = await request.json()
        now += float(payload.get("seconds", 0))
        if "wall_now" in payload:
            wall_now = datetime.fromisoformat(payload["wall_now"])

        return web.json_response({"now": now})

    app = web.Application()
    app.router.add_post("/advance", advance)
    runner = web.AppRunner(app)
    await runner.setup()

    try:
        await web.TCPSite(runner, "127.0.0.1", port).start()
        await start_polling(
            main_keyboard_clock=lambda: now,
            subscription_broadcast_now=lambda: wall_now or datetime.now(UTC),
            admin_broadcast_now=lambda: wall_now or datetime.now(UTC),
        )
    finally:
        await runner.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--wall-now", type=datetime.fromisoformat)
    args = parser.parse_args()

    asyncio.run(run(args.port, args.wall_now))


if __name__ == "__main__":
    main()
