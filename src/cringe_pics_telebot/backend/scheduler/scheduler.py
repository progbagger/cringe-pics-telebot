from cringe_pics_telebot.backend.misc import ImageManager
from cringe_pics_telebot.db import DatabaseManager
from cringe_pics_telebot.orm import User


class Scheduler:
    def __init__(self, db_manager: DatabaseManager) -> None:
        self._database_manager = db_manager

    async def run(self) -> None:
        pass

    def _is_time_to_send(self, user: User) -> bool:
        if user.last_sent_scheduled_message_date is None:
            return True
