import re

MAX_EXTRA_RECIPIENTS = 1000
_MAX_TELEGRAM_USER_ID = 2**63 - 1


class InvalidAdminBroadcastRecipientsError(ValueError): ...


def parse_admin_broadcast_recipient_ids(value: str) -> set[int]:
    normalized = value.strip()
    if normalized == "-":
        return set()

    parts = [part for part in re.split(r"[\s,;]+", normalized) if part]
    if not parts:
        raise InvalidAdminBroadcastRecipientsError("At least one ID or '-' is required")
    if len(parts) > MAX_EXTRA_RECIPIENTS:
        raise InvalidAdminBroadcastRecipientsError(f"At most {MAX_EXTRA_RECIPIENTS} IDs are allowed")

    try:
        user_ids = {int(part) for part in parts}
    except ValueError as error:
        raise InvalidAdminBroadcastRecipientsError("Every recipient ID must be an integer") from error

    if any(user_id <= 0 or user_id > _MAX_TELEGRAM_USER_ID for user_id in user_ids):
        raise InvalidAdminBroadcastRecipientsError("Recipient IDs must be positive Telegram user IDs")
    return user_ids
