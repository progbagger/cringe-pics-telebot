import logging
import os

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from cringe_pics_telebot.repositories.postgres import connect as connect_postgres
from cringe_pics_telebot.repositories.postgres import create_tables
from cringe_pics_telebot.repositories.yandex import connect as connect_yandex

from .dispatcher import dp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


async def start_polling() -> None:
    logger.info("Creating bot")
    bot = Bot(
        os.environ["TELEGRAM_BOT_TOKEN"],
        default=DefaultBotProperties(parse_mode=ParseMode.HTML.value),
    )
    logger.info("Bot created!")

    logger.info("Connecting to the database...")
    username = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "0.0.0.0")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    connect_postgres(
        username=username,
        password=password,
        database=database,
        port=port,
        host=host,
    )
    logger.info("Connected to the database!")

    logger.info("Createing tables...")
    await create_tables()
    logger.info("Tables created!")

    logger.info("Connecting to Yandex...")
    try:
        yandex_key = os.environ["YANDEX_DISK_TOKEN"]
    except KeyError:
        logger.exception("Failed to get Yandex token")
        raise
    else:
        connect_yandex(yandex_key)
    logger.info("Connected to the Yandex!")

    logger.info("Polling...")
    await dp.start_polling(bot)
