from dataclasses import dataclass
from repositories.yandex_disk.entities.resource import Resource


@dataclass(slots=True)
class ResourceList:
    """Список ресурсов, содержащихся в папке"""

    sort: str | None
    """Поле, по которому отсортирован список"""
    items: list[Resource]
    """Список ресурсов в папке"""
    limit: int
    """Максимальное количество элементов в списке items"""
    offset: int
    """Смещение относительно первого элемента в папке"""
    path: str
    """Путь к папке на диске"""
    total: int
    """Общее количество ресурсов в папке"""
