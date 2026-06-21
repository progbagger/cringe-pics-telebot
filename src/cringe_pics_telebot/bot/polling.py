import logging
import os
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, asynccontextmanager

from cringe_pics_telebot.bot import create_bot, dp
from cringe_pics_telebot.repositories.postgres import connect as connect_postgres
from cringe_pics_telebot.repositories.postgres import create_tables
from cringe_pics_telebot.repositories.redis import connect as connect_redis
from cringe_pics_telebot.repositories.yandex import connect as connect_yandex

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@asynccontextmanager
async def _connect_postgres() -> AsyncGenerator:
    logger.info("Connecting to the database...")
    username = os.environ.get("POSTGRES_USER", "postgres")
    password = os.environ.get("POSTGRES_PASSWORD", "postgres")
    host = os.environ.get("POSTGRES_HOST", "0.0.0.0")
    port = int(os.environ.get("POSTGRES_PORT", "5432"))
    database = os.environ.get("POSTGRES_DB", "postgres")
    async with connect_postgres(
        username=username,
        password=password,
        database=database,
        port=port,
        host=host,
    ):
        logger.info("Connected to the database!")

        logger.info("Creating tables...")
        await create_tables()
        logger.info("Tables created!")

        yield


@asynccontextmanager
async def _connect_yandex() -> AsyncGenerator:
    logger.info("Connecting to Yandex...")
    try:
        yandex_key = os.environ["YANDEX_DISK_TOKEN"]
    except KeyError:
        logger.exception("Failed to get Yandex token")
        raise
    else:
        connect_yandex(yandex_key)
    logger.info("Connected to the Yandex!")

    yield


@asynccontextmanager
async def _connect_redis() -> AsyncGenerator:
    logger.info("Connecting to Redis...")
    username = os.environ.get("REDIS_USER", "default")
    password = os.environ.get("REDIS_PASSWORD", "redis")
    host = os.environ.get("REDIS_HOST", "0.0.0.0")
    port = int(os.environ.get("REDIS_PORT", "6379"))

    async with connect_redis(username=username, password=password, host=host, port=port):
        logger.info("Connected to Redis!")

    yield


async def start_polling() -> None:
    connectors = (_connect_postgres, _connect_yandex, _connect_redis)
    async with AsyncExitStack() as stack:
        for connector in connectors:
            await stack.enter_async_context(connector())

        logger.info("Polling...")
        try:
            bot = create_bot(os.environ["TELEGRAM_BOT_TOKEN"])
        except KeyError:
            logger.exception("Failed to get Telegram bot token")
            raise
        await dp.start_polling(bot)
