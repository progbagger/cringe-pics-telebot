from dataclasses import dataclass


@dataclass
class YandexDiskImagePath:
    """Класс для хранения пути к изображению на диске"""

    path: str
    """Путь к изображению на диске"""
    mime_type: str | None = None
    """MIME-тип изображения"""


@dataclass
class Image:
    """Класс для хранения информации об изображении"""

    data: bytes
    """Данные изображения"""
    format: str | None = None
    """Формат изображения"""
