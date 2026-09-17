"""Start the real bot with only the subscription scheduler's clock injected."""

import argparse
import asyncio
from datetime import datetime

from cringe_pics_telebot.cli import start_polling


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--now", required=True)
    args = parser.parse_args()
    now = datetime.fromisoformat(args.now)

    asyncio.run(start_polling(subscription_broadcast_now=lambda: now))


if __name__ == "__main__":
    main()
