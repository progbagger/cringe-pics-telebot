from datetime import timedelta

import pytest
from hamcrest import assert_that, equal_to, has_properties, none

from cringe_pics_telebot.services.media_alias_enrichment_settings import (
    MediaAliasEnrichmentSettings,
    load_media_alias_enrichment_settings,
)


def test_disabled_settings_ignore_ollama_configuration() -> None:
    settings = load_media_alias_enrichment_settings(
        {
            "MEDIA_ALIAS_ENRICHMENT_ENABLED": "false",
            "OLLAMA_REQUEST_TIMEOUT_SECONDS": "not-a-number",
        }
    )

    assert_that(settings, equal_to(MediaAliasEnrichmentSettings()))
    assert_that(settings.ollama_base_url, none())


def test_enabled_settings_are_normalized_and_keep_secrets_out_of_repr() -> None:
    settings = load_media_alias_enrichment_settings(
        {
            "MEDIA_ALIAS_ENRICHMENT_ENABLED": "YES",
            "OLLAMA_BASE_URL": "https://ollama.example.com/",
            "OLLAMA_MODEL": "gemma3:12b",
            "OLLAMA_API_KEY": "secret-token",
            "OLLAMA_REQUEST_TIMEOUT_SECONDS": "2.5",
            "MEDIA_ALIAS_LLM_PROMPT": "Опиши изображение",
            "MEDIA_ALIAS_ENRICHMENT_CONCURRENCY": "3",
            "MEDIA_ALIAS_ENRICHMENT_LEASE_TTL_SECONDS": "20",
            "MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH_SECONDS": "5",
            "MEDIA_ALIAS_ENRICHMENT_RETRY_BASE_SECONDS": "4",
            "MEDIA_ALIAS_ENRICHMENT_RETRY_MAX_SECONDS": "40",
        }
    )

    assert_that(
        settings,
        has_properties(
            enabled=True,
            ollama_base_url="https://ollama.example.com",
            ollama_model="gemma3:12b",
            ollama_request_timeout=timedelta(seconds=2.5),
            concurrency=3,
            lease_ttl=timedelta(seconds=20),
            lease_refresh=timedelta(seconds=5),
            retry_base=timedelta(seconds=4),
            retry_max=timedelta(seconds=40),
        ),
    )
    assert_that(
        settings.prompt_sha256,
        equal_to("ad8e7c2c96f23bbde8e0a91440c8a3df49c10d71d4f92320db9d05c703d8eef6"),
    )
    assert "secret-token" not in repr(settings)
    assert "Опиши изображение" not in repr(settings)


@pytest.mark.parametrize("missing", ["OLLAMA_BASE_URL", "OLLAMA_MODEL", "MEDIA_ALIAS_LLM_PROMPT"])
def test_enabled_settings_require_ollama_values(missing: str) -> None:
    environ = {
        "MEDIA_ALIAS_ENRICHMENT_ENABLED": "true",
        "OLLAMA_BASE_URL": "http://ollama:11434",
        "OLLAMA_MODEL": "gemma3:12b",
        "MEDIA_ALIAS_LLM_PROMPT": "Опиши изображение",
    }
    del environ[missing]

    with pytest.raises(ValueError, match=missing):
        load_media_alias_enrichment_settings(environ)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("MEDIA_ALIAS_ENRICHMENT_ENABLED", "perhaps", "boolean"),
        ("OLLAMA_BASE_URL", "https://user:pass@ollama.example.com", "root URL"),
        ("OLLAMA_BASE_URL", "https://ollama.example.com/api", "root URL"),
        ("MEDIA_ALIAS_ENRICHMENT_CONCURRENCY", "0", "positive"),
        ("OLLAMA_REQUEST_TIMEOUT_SECONDS", "never", "number"),
        ("OLLAMA_REQUEST_TIMEOUT_SECONDS", "nan", "positive"),
        ("MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH_SECONDS", "300", "less than lease TTL"),
        ("MEDIA_ALIAS_ENRICHMENT_RETRY_MAX_SECONDS", "29", "greater than or equal"),
    ],
)
def test_invalid_settings_are_rejected(*, name: str, value: str, message: str) -> None:
    environ = {
        "MEDIA_ALIAS_ENRICHMENT_ENABLED": "true",
        "OLLAMA_BASE_URL": "http://ollama:11434",
        "OLLAMA_MODEL": "gemma3:12b",
        "MEDIA_ALIAS_LLM_PROMPT": "Опиши изображение",
        name: value,
    }

    with pytest.raises(ValueError, match=message):
        load_media_alias_enrichment_settings(environ)
