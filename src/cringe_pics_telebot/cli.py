import asyncio
import logging
import os

import click

from cringe_pics_telebot.bot.polling import start_polling

logging.basicConfig(level=logging.INFO)

log_level_name = os.environ.get("LOG_LEVEL_NAME", "INFO").upper()
try:
    log_level = logging.getLevelNamesMapping()[log_level_name]
except KeyError:
    logging.exception("Failed to set log level %s", log_level_name)
    log_level = logging.INFO

logging.getLogger().setLevel(log_level)


@click.command()
def poll() -> None:
    asyncio.run(start_polling())
