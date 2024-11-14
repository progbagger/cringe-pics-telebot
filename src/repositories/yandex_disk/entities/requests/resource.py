from dataclasses import dataclass


@dataclass(slots=True)
class Resource:
    """Класс для запроса ресурса"""

    href: str
    """Ссылка на ресурс"""
