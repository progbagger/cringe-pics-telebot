from dataclasses import dataclass


@dataclass(slots=True)
class Link:
    """Объект с URL для запроса метаданных ресурса"""

    href: str
    """Ссылка на скачивание файла"""
