from collections.abc import Sequence
from dataclasses import dataclass

INLINE_KEYBOARD_PAGE_ROWS = 8


@dataclass(frozen=True, slots=True)
class InlineKeyboardPage[T]:
    items: tuple[T, ...]
    number: int
    previous_number: int | None
    next_number: int | None


def paginate_inline_keyboard[T](
    items: Sequence[T],
    requested_page: int,
    *,
    rows_per_item: int = 1,
) -> InlineKeyboardPage[T]:
    if not 1 <= rows_per_item <= INLINE_KEYBOARD_PAGE_ROWS:
        raise ValueError(f"rows_per_item must be in range 1..{INLINE_KEYBOARD_PAGE_ROWS}")

    items_per_page = INLINE_KEYBOARD_PAGE_ROWS // rows_per_item
    last_page = max(0, (len(items) - 1) // items_per_page)
    page_number = min(max(requested_page, 0), last_page)
    start = page_number * items_per_page
    page_items = tuple(items[start : start + items_per_page])

    return InlineKeyboardPage(
        items=page_items,
        number=page_number,
        previous_number=page_number - 1 if page_number > 0 else None,
        next_number=page_number + 1 if page_number < last_page else None,
    )
