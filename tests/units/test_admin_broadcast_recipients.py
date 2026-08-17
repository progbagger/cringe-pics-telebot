import pytest

from cringe_pics_telebot.services.admin_broadcast_recipients import (
    MAX_EXTRA_RECIPIENTS,
    InvalidAdminBroadcastRecipientsError,
    parse_admin_broadcast_recipient_ids,
)


def test_parse_recipient_ids_with_supported_separators_and_duplicates() -> None:
    assert parse_admin_broadcast_recipient_ids("700, 400\n700;900") == {400, 700, 900}


def test_dash_clears_recipient_ids() -> None:
    assert parse_admin_broadcast_recipient_ids(" - ") == set()


@pytest.mark.parametrize("value", ["", "abc", "-1", "0", str(2**63)])
def test_reject_invalid_recipient_ids(value: str) -> None:
    with pytest.raises(InvalidAdminBroadcastRecipientsError):
        parse_admin_broadcast_recipient_ids(value)


def test_reject_too_many_recipient_ids() -> None:
    value = " ".join(str(user_id) for user_id in range(1, MAX_EXTRA_RECIPIENTS + 2))
    with pytest.raises(InvalidAdminBroadcastRecipientsError):
        parse_admin_broadcast_recipient_ids(value)
