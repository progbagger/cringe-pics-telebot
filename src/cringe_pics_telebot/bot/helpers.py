from typing import Protocol, runtime_checkable


@runtime_checkable
class HasFileId(Protocol):
    file_id: str
