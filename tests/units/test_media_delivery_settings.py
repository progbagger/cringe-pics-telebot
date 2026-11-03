import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendPhoto

from cringe_pics_telebot.services.media_delivery import is_media_rejection
from cringe_pics_telebot.services.media_delivery_settings import load_media_delivery_max_retries


@pytest.mark.parametrize("raw,expected", [(None, 3), ("0", 0), ("1", 1), ("7", 7)])
def test_delivery_retry_budget_counts_retries_after_first_attempt(raw: str | None, expected: int) -> None:
    environ = {} if raw is None else {"MEDIA_DELIVERY_MAX_RETRIES": raw}
    assert load_media_delivery_max_retries(environ) == expected


@pytest.mark.parametrize("raw", ["-1", "1.5", "", "invalid", "true"])
def test_invalid_retry_configuration_names_setting(raw: str) -> None:
    with pytest.raises(ValueError, match="MEDIA_DELIVERY_MAX_RETRIES must be a non-negative integer"):
        load_media_delivery_max_retries({"MEDIA_DELIVERY_MAX_RETRIES": raw})


@pytest.mark.parametrize(
    "message,expected",
    [
        ("Bad Request: failed to get HTTP URL content", True),
        ("Bad Request: wrong file identifier/HTTP URL specified", True),
        ("Bad Request: wrong remote file identifier specified", True),
        ("Bad Request: wrong type of the web page content", True),
        ("Bad Request: WEBPAGE_CURL_FAILED", True),
        ("Bad Request: IMAGE_PROCESS_FAILED", True),
        ("Bad Request: PHOTO_INVALID_DIMENSIONS", True),
        ("Bad Request: VIDEO_CONTENT_TYPE_INVALID", True),
        ("Bad Request: failed to process video", True),
        ("Bad Request: chat not found", False),
        ("Bad Request: message media can't be edited", False),
        ("Bad Request: message to edit not found", False),
        ("Bad Request: unknown error", False),
    ],
)
def test_only_known_media_rejections_allow_rotation(message: str, expected: bool) -> None:
    assert (
        is_media_rejection(TelegramBadRequest(method=SendPhoto(chat_id=42, photo="file"), message=message)) == expected
    )


@pytest.mark.parametrize("kind", ["forbidden", "network", "rate_limit", "unexpected"])
def test_other_error_types_do_not_allow_rotation_even_with_media_error_text(kind: str) -> None:
    method = SendPhoto(chat_id=42, photo="file")
    message = "failed to get HTTP URL content"
    error = {
        "forbidden": TelegramForbiddenError(method=method, message=message),
        "network": TelegramNetworkError(method=method, message=message),
        "rate_limit": TelegramRetryAfter(method=method, message=message, retry_after=1),
        "unexpected": RuntimeError(message),
    }[kind]
    assert not is_media_rejection(error)
