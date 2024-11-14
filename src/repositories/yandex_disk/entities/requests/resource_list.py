from dataclasses import dataclass
from enum import StrEnum


class Sort(StrEnum):
    """Атрибут, по которому сортируется список ресурсов"""

    name = "name"
    """Имя ресурса"""
    path = "path"
    """Путь к ресурсу"""
    created = "created"
    """Дата создания ресурса"""
    modified = "modified"
    """Дата изменения ресурса"""
    size = "size"
    """Размер ресурса"""


@dataclass(slots=True)
class ResourceList:
    """Класс запроса для ресурса на диске"""

    path: str
    """Путь к объекту на диске"""
    fields: list[str] | None = None
    """Список полей, которые нужно включить в ответ"""
    limit: int | None
    """Количество ресурсов, описание которых нужно вернуть"""
    offset: int | None
    """Количество ресурсов, которые стоит опустить"""
    sort: Sort | None
    """Атрибут, по которому сортируется список ресурсов"""
