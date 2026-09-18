from collections.abc import Callable
from time import monotonic

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from cringe_pics_telebot.services.main_keyboard import MainKeyboardCache

from .admin_broadcasts import router as admin_broadcasts_router
from .admin_categories import router as admin_categories_router
from .admin_media import router as admin_media_router
from .admin_panel import router as admin_panel_router
from .images import router as images_router
from .inline import router as inline_router
from .main_keyboard import MainKeyboardMiddleware
from .user_registration import RegisterPrivateUserMiddleware

dp = Dispatcher()
dp.message.outer_middleware(RegisterPrivateUserMiddleware())
dp.include_router(inline_router)
dp.include_router(admin_panel_router)
dp.include_router(admin_broadcasts_router)
dp.include_router(admin_categories_router)
dp.include_router(admin_media_router)
dp.include_router(images_router)


def create_bot(
    token: str,
    *,
    api_base_url: str | None = None,
    main_keyboard_clock: Callable[[], float] = monotonic,
) -> Bot:
    session = None
    if api_base_url is not None:
        session = AiohttpSession(api=TelegramAPIServer.from_base(api_base_url))

    bot = Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML.value))
    bot.session.middleware(MainKeyboardMiddleware(MainKeyboardCache(clock=main_keyboard_clock)))

    return bot
