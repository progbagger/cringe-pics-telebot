from telebot import TeleBot
from telebot.types import (
    Message,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
import os
import logging

TOKEN = os.environ.get("TELEBOT_TOKEN")
if TOKEN is None:
    print('Environment variable "TELEBOT_TOKEN" is not set. Exiting with code 1.')
    exit(1)

bot = TeleBot(TOKEN, parse_mode="HTML")

users = {}

CATEGORIES = [
    "Утренняя (9:00)",
    "Вечерняя (18:00)",
    "Ночная (23:00)",
    "Ежедневная (12:00)",
]

SUBSCRIBED = "✅"
UNSUBSCRIBED = "❌"


@bot.message_handler(commands=["start", "help", "commands"])
@bot.message_handler(regexp=r"(?i)помощь|команд|help")
def send_help(message: Message):
    bot.send_message(
        message.chat.id,
        reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("Список подписок"),
        text=f"""\
<b>Приветствую, <i>{message.from_user.full_name}</i>!</b>

Я - бот, который будет присылать тебе <b>кринжульки из WhatsApp</b>, если ты, конечно, подпишешься на рассылку...
Вот, что я умею:

<code>/list</code> - <i>показать список доступных рассылок. Тут же можно будет отписаться/подписаться на них.</i>\

<i>inline</i> использование (доступно в любом чате):

<code>/morning</code> - <i>отправить картинку <b>С добрым утром!</b></i>
<code>/evening</code> - <i>отправить картинку <b>Хорошего вечера!</b></i>
<code>/night</code> - <i>отправить картинку <b>Доброй ночи!</b></i>
<code>/day</code> - <i>отправить картинку <b>С днём дня!</b></i>
""",
    )


def create_inline_categories_markup(id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    for category in CATEGORIES:
        try:
            users[id][category]
            markup.add(
                InlineKeyboardButton(
                    f"{category} {SUBSCRIBED}", callback_data=category
                ),
            )
        except:
            markup.add(
                InlineKeyboardButton(
                    f"{category} {UNSUBSCRIBED}", callback_data=category
                )
            )
    return markup


@bot.message_handler(commands=["list"])
@bot.message_handler(regexp=r"(?i)список подписок")
def send_list(message: Message):
    bot.send_message(
        message.chat.id,
        """\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.""",
        reply_markup=create_inline_categories_markup(message.from_user.id),
    )


@bot.callback_query_handler(func=lambda call: call.data in CATEGORIES)
def manage_subscriptions(call: CallbackQuery):
    try:
        subscriptions = users[call.from_user.id]
    except:
        subscriptions = users[call.from_user.id] = {}

    try:
        del subscriptions[call.data]
    except:
        subscriptions[call.data] = True

    if subscriptions is None:
        del users[call.from_user.id]

    bot.edit_message_reply_markup(
        call.message.chat.id,
        call.message.id,
        reply_markup=create_inline_categories_markup(call.from_user.id),
    )


def main():
    bot.enable_save_next_step_handlers()
    bot.enable_save_reply_handlers()
    bot.enable_saving_states()

    bot.load_next_step_handlers()
    bot.load_reply_handlers()

    bot.infinity_polling(logger_level=logging.INFO)


if __name__ == "__main__":
    main()
