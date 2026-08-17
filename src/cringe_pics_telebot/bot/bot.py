from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from .images import router as images_router
from .inline import router as inline_router
from .user_registration import RegisterPrivateUserMiddleware

dp = Dispatcher()
dp.message.outer_middleware(RegisterPrivateUserMiddleware())
dp.include_router(inline_router)
dp.include_router(images_router)


def create_bot(token: str, *, api_base_url: str | None = None) -> Bot:
    session = None
    if api_base_url is not None:
        session = AiohttpSession(api=TelegramAPIServer.from_base(api_base_url))

    return Bot(token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML.value))
