import asyncio

import click

from cringe_pics_telebot.services.polling import start_polling


@click.command()
def poll() -> None:
    asyncio.run(start_polling())
