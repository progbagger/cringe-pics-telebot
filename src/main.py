from telebot.async_telebot import AsyncTeleBot
from telebot.types import (
    Message,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
import os
import logging
import psycopg2 as pg2
import asyncio
import time
import random

TOKEN = os.environ.get("TELEBOT_TOKEN")
if TOKEN is None:
    print('Environment variable "TELEBOT_TOKEN" is not set. Exiting with code 1.')
    exit(1)

BOT = AsyncTeleBot(TOKEN, parse_mode="HTML")

USERS = {}
USERS_FILENAME = "users.json"

BASE_DIRECTORY = os.path.dirname(os.path.realpath(__file__)) + "/../"
ASSETS_DIRECTORY = BASE_DIRECTORY + "assets/"

CATEGORIES = {
    "morning": "Утренняя (9:00)",
    "evening": "Вечерняя (18:00)",
    "night": "Ночная (23:00)",
    "day": "Ежедневная (12:00)",
}

CATEGORIES_TO_BUTTONS = {
    "morning": "Утро",
    "evening": "Вечер",
    "night": "Ночь",
    "day": "День",
    "random": "Случайно",
}

SUBSCRIBED = "✅"
UNSUBSCRIBED = "❌"

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", 5432)
POSTGRES_USERNAME = os.environ.get("POSTGRES_USERNAME", "postgres")
POSTGRES_PASSWORD = os.environ.get("POSTGRES_PASSWORD")
POSTGRES_NAME = os.environ.get("POSTGRES_NAME", "cringe")
POSTGRES_TABLE = "users"
POSTGRES_CONNECTION = None

REPLY_MARKUP = (
    ReplyKeyboardMarkup(resize_keyboard=True)
    .add("Список подписок", row_width=1)
    .add(*CATEGORIES_TO_BUTTONS.values())
)


@BOT.message_handler(commands=["start", "help", "commands"])
@BOT.message_handler(regexp=r"(?i)помощь|команд|help")
async def send_help(message: Message):
    asyncio.gather(
        BOT.send_message(
            message.chat.id,
            reply_markup=REPLY_MARKUP,
            text=f"""\
<b>Приветствую, <i>{message.from_user.full_name}</i>!</b>

Я - бот, который будет присылать тебе <b>кринжульки из WhatsApp</b>, если ты, конечно, подпишешься на рассылку...
Вот, что я умею:

<code>/list</code> - <i>показать список доступных рассылок. Тут же можно будет отписаться/подписаться на них.</i>\

<i>inline</i> использование (доступно в любом чате):

<code>/morning</code> - <i>отправить картинку <b>С добрым утром!</b></i>
<code>/evening</code> - <i>отправить картинку <b>Хорошего вечера!</b></i>
<code>/night</code> - <i>отправить картинку <b>Доброй ночи!</b></i>
<code>/day</code> - <i>отправить картинку в духе <b>С днём дня!</b></i>
<code>/random</code> - <i>отправить случайную картинку из предыдущих категорий</i>
""",
        )
    )


def create_inline_categories_markup(id: int) -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=1)
    cursor = POSTGRES_CONNECTION.cursor()
    cursor.execute(
        f"SELECT {', '.join(CATEGORIES.keys())} FROM {POSTGRES_TABLE} WHERE id = {id}"
    )
    subscriptions = cursor.fetchone()
    cursor.close()

    for num, category in enumerate(CATEGORIES.items()):
        markup.add(
            InlineKeyboardButton(
                f"{category[-1]} {UNSUBSCRIBED if not subscriptions or not subscriptions[num] else SUBSCRIBED}",
                callback_data=category[0],
            ),
        )

    return markup


@BOT.message_handler(commands=["list"])
@BOT.message_handler(regexp=r"(?i)список подписок")
async def send_list(message: Message):
    asyncio.gather(
        BOT.send_message(
            message.chat.id,
            """\
Вот <b>список</b> твоих подписок.

<b>Кликни</b> на подписку, чтобы <b>подписаться/отписаться</b> от рассылки.

<i>Время по Новосибирску (GMT +7, MSK +4)</i>\
""",
            reply_markup=create_inline_categories_markup(message.from_user.id),
        )
    )


@BOT.callback_query_handler(func=lambda call: call.data in CATEGORIES.keys())
async def manage_subscriptions(call: CallbackQuery):
    cursor = POSTGRES_CONNECTION.cursor()
    try:
        cursor.execute(
            f"INSERT INTO {POSTGRES_TABLE} (id) VALUES (%s)", (call.from_user.id,)
        )
    except Exception:
        pass
    POSTGRES_CONNECTION.commit()

    cursor.execute(
        f"SELECT {', '.join(CATEGORIES.keys())} FROM {POSTGRES_TABLE} WHERE id = {call.from_user.id}"
    )

    subscriptions = cursor.fetchone()
    map = {"morning": 0, "evening": 1, "night": 2, "day": 3}
    cursor.execute(
        f"UPDATE {POSTGRES_TABLE} SET {call.data} = %s WHERE id = {call.from_user.id}",
        (str(not subscriptions[map[call.data]]).lower(),),
    )
    POSTGRES_CONNECTION.commit()

    cursor.close()

    asyncio.gather(
        BOT.edit_message_reply_markup(
            call.message.chat.id,
            call.message.id,
            reply_markup=create_inline_categories_markup(call.from_user.id),
        )
    )


IMAGE_FORMAT = (".jpg", ".jpeg", ".png")
GIF_FORMAT = (".gif", ".webp")
ALL_FORMATS = (*IMAGE_FORMAT, *GIF_FORMAT)


async def send_photo(id: int, folder: str, image_name: str, category: str):
    if image_name:
        with open(f"{folder}/{image_name}", "rb") as image_to_send:
            if image_name.endswith(GIF_FORMAT):
                await BOT.send_animation(id, image_to_send, reply_markup=REPLY_MARKUP)
            else:
                await BOT.send_photo(id, image_to_send, reply_markup=REPLY_MARKUP)
    else:
        await BOT.send_message(
            id,
            f"В категории <b>{CATEGORIES[category]}</b> пока нет картинок, но они скоро появятся. Обязательно появятся.",
            reply_markup=REPLY_MARKUP,
        )


@BOT.message_handler(commands=[*CATEGORIES.keys(), "random"])
async def send_random_categorized_photo(message: Message):
    command = message.text.split()[0].strip("/").lower()
    if command == "random":
        command = random.choice(list(CATEGORIES.keys()))
    folder = ASSETS_DIRECTORY + command
    images = list(
        filter(
            lambda name: os.path.isfile(f"{folder}/{name}")
            and name.endswith(ALL_FORMATS),
            os.listdir(folder),
        )
    )

    try:
        image = random.choice(images)
    except:
        image = None
    asyncio.gather(send_photo(message.chat.id, folder, image, command))


async def send_pictures(category: str):
    folder = ASSETS_DIRECTORY + category
    contents = os.listdir(folder)
    images = list(
        filter(
            lambda name: os.path.isfile(f"{folder}/{name}")
            and name.endswith(ALL_FORMATS),
            contents,
        )
    )

    cursor = POSTGRES_CONNECTION.cursor()
    cursor.execute(f"SELECT id FROM {POSTGRES_TABLE} WHERE {category} = true")
    candidates = [res[0] for res in cursor.fetchall()]
    cursor.close()

    for candidate in candidates:
        image_to_send = random.choice(images)
        asyncio.gather(send_photo(candidate, folder, image_to_send, category))


async def schedule_messages():
    times_and_categories = [
        (cat[-1].split()[-1].strip("()"), cat[0]) for cat in CATEGORIES.items()
    ]
    times_in_seconds = []
    for time_in_seconds in times_and_categories:
        hm = time_in_seconds[0].split(":")
        times_in_seconds.append(
            (int(hm[0]) * 60 * 60 + int(hm[-1]) * 60, time_in_seconds[-1])
        )

    while True:
        current_seconds = int(time.time() + 7 * 60 * 60) % (60 * 60 * 24)
        print(f"Current seconds: {current_seconds}")
        current_to_wait = times_in_seconds[0]
        current_diff = (
            24 * 60 * 60 - current_seconds + min([tup[0] for tup in times_in_seconds])
        )
        for w in times_in_seconds:
            if w[0] == min([tup[0] for tup in times_in_seconds]):
                current_to_wait = w

        for wait_time in times_in_seconds:
            if (
                wait_time[0] > current_seconds
                and abs(current_seconds - wait_time[0]) < current_diff
            ):
                current_diff = abs(current_seconds - wait_time[0])
                current_to_wait = wait_time

        print(
            f"Sleeping for {current_diff} seconds to reach {current_to_wait[0]} with category {current_to_wait[-1]}"
        )
        await asyncio.sleep(current_diff)
        asyncio.gather(send_pictures(current_to_wait[-1]))
        await asyncio.sleep(5)


@BOT.message_handler(content_types=["text"])
@BOT.message_handler(regexp=f"(?i){'|'.join([*CATEGORIES_TO_BUTTONS.values()])}")
async def send_pictures_on_button(message: Message):
    message.text = {v: k for k, v in CATEGORIES_TO_BUTTONS.items()}[
        message.text.capitalize()
    ]

    await send_random_categorized_photo(message)


@BOT.message_handler(content_types=["text"])
async def send_anything(message: Message):
    await BOT.send_message(
        message.chat.id,
        """\
Я не знаю такой команды.

Если хочешь пообщаться - я уверен, у тебя есть родственники в WhatsApp :)\
""",
        reply_markup=REPLY_MARKUP,
    )


async def main():
    BOT.enable_saving_states()

    try:
        global POSTGRES_CONNECTION
        POSTGRES_CONNECTION = pg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_NAME,
            user=POSTGRES_USERNAME,
            password=POSTGRES_PASSWORD,
            # async_=True,
        )

        cursor = POSTGRES_CONNECTION.cursor()
        cursor.execute(
            f"""\
CREATE TABLE IF NOT EXISTS {POSTGRES_TABLE} (
    id BIGINT PRIMARY KEY UNIQUE NOT NULL,
    {', '.join(f'{cat} BOOLEAN NOT NULL DEFAULT false' for cat in CATEGORIES.keys())}
)"""
        )
        cursor.close()

        tasks = [
            asyncio.create_task(BOT.infinity_polling(logger_level=logging.INFO)),
            asyncio.create_task(schedule_messages()),
        ]
        for task in tasks:
            await task
    except (Exception, KeyboardInterrupt) as e:
        print(e.__class__.__name__)
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
