import asyncio

import click

from cringe_pics_telebot.bot import start_polling


@click.command()
def poll() -> None:
    asyncio.run(start_polling())


if __name__ == "__main__":
    poll()
