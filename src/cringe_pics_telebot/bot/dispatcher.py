from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from cringe_pics_telebot.bot.keyboards import (
    create_inline_subscriptions_keyboard,
    create_reply_keyboard,
)
from cringe_pics_telebot.repositories.postgres.subscription_types import (
    get_subscription_types,
)
from cringe_pics_telebot.services.subscriptions import get_user_subscriptions

dp = Dispatcher()


@dp.message(Command("start", "help"))
async def handle_start(message: Message) -> None:
    assert message.from_user

    subscription_types = await get_subscription_types()
    text = f"""\
<b>Приветствую, <i>{message.from_user.first_name}</i>!</b>

Я - бот, который будет присылать тебе <b>кринжульки из WhatsApp</b>, \
если ты, конечно, подпишешься на рассылку...
Вот, что я умею:

<code>/list</code> - <i>показать список доступных рассылок. \
Тут же можно будет отписаться/подписаться на них.</i>\

<i>inline</i> использование (доступно в любом чате):

<code>/morning</code> - <i>отправить картинку <b>С добрым утром!</b></i>
<code>/evening</code> - <i>отправить картинку <b>Хорошего вечера!</b></i>
<code>/night</code> - <i>отправить картинку <b>Доброй ночи!</b></i>
<code>/day</code> - <i>отправить картинку в духе <b>С днём дня!</b></i>
<code>/random</code> - <i>отправить случайную картинку из предыдущих категорий</i>\
"""

    await message.answer(
        text=text,
        reply_markup=create_reply_keyboard(subscription_types),
    )


@dp.message(Command("list", "subscriptions"))
@dp.message(F.text.lower() == "подписки")
async def show_subscriptions(message: Message) -> None:
    assert message.from_user

    subscriptions = await get_user_subscriptions(message.from_user.id)
    await message.answer(
        text="""\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время по Новосибирску (GMT +7, MSK +4)</i>\
""",
        reply_markup=create_inline_subscriptions_keyboard(subscriptions),
    )
