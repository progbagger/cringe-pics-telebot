from dataclasses import dataclass
import datetime
from enum import StrEnum


class Type(StrEnum):
    """Тип ресурса"""
    
    dir = "dir"
    """Папка"""
    file = "file"
    """Файл"""


@dataclass(slots=True)
class Resource:
    """Описание ресурса"""

    name: str
    """Имя ресурса"""
    created: datetime.datetime
    """Дата и время создания ресурса"""
    modified: datetime.datetime
    """Дата и время изменения ресурса"""
    path: str
    """Путь к файлу на диске"""
    md5: str
    """MD5-хэш файла"""
    type: Type
    """Тип ресурса"""
    mime_type: str
    """MIME-тип файла"""
    size: int
    """Размер файла"""
