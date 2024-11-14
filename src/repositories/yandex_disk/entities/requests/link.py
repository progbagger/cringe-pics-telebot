from dataclasses import dataclass


@dataclass(slots=True)
class Link:
    """Класс для запроса ссылки на ресурс"""

    path: str
    """Путь к ресурсу на диске"""
    fields: list[str] | None
    """Поля, которые нужно включить в ответ"""
