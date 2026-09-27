from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class AnnualDate:
    month: int
    day: int

    def __post_init__(self) -> None:
        try:
            date(2000, self.month, self.day)
        except ValueError as error:
            raise ValueError("Annual date must contain a valid month and day") from error

    def format(self) -> str:
        return f"{self.day:02d}.{self.month:02d}"
