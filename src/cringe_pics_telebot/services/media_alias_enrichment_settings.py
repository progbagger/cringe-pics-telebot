import asyncio
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from hashlib import sha256
from math import isfinite
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_OLLAMA_REQUEST_TIMEOUT = timedelta(seconds=120)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_CONCURRENCY = 2
DEFAULT_MEDIA_ALIAS_ENRICHMENT_POLL_INTERVAL = timedelta(seconds=5)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_LEASE_TTL = timedelta(seconds=300)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH = timedelta(seconds=60)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_ATTEMPTS = 5
DEFAULT_MEDIA_ALIAS_ENRICHMENT_RETRY_BASE = timedelta(seconds=30)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_RETRY_MAX = timedelta(seconds=3600)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_DOWNLOAD_TIMEOUT = timedelta(seconds=60)
DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_SOURCE_BYTES = 20 * 1024 * 1024
DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_FRAME_PIXELS = 40_000_000
DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_EDGE_PIXELS = 1280
DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_MEDIA_ALIAS_LLM_PROMPT_FILE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MediaAliasEnrichmentSettings:
    enabled: bool = False
    ollama_base_url: str | None = None
    ollama_model: str | None = None
    ollama_api_key: str | None = field(default=None, repr=False)
    ollama_request_timeout: timedelta = DEFAULT_OLLAMA_REQUEST_TIMEOUT
    prompt: str | None = field(default=None, repr=False)
    concurrency: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_CONCURRENCY
    poll_interval: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_POLL_INTERVAL
    lease_ttl: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_LEASE_TTL
    lease_refresh: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH
    max_attempts: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_ATTEMPTS
    retry_base: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_RETRY_BASE
    retry_max: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_RETRY_MAX
    download_timeout: timedelta = DEFAULT_MEDIA_ALIAS_ENRICHMENT_DOWNLOAD_TIMEOUT
    max_source_bytes: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_SOURCE_BYTES
    max_frame_pixels: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_FRAME_PIXELS
    max_image_edge_pixels: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_EDGE_PIXELS
    max_image_bytes: int = DEFAULT_MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_BYTES

    @property
    def prompt_sha256(self) -> str | None:
        return sha256(self.prompt.encode()).hexdigest() if self.prompt is not None else None


async def load_media_alias_enrichment_settings(
    environ: Mapping[str, str] | None = None,
) -> MediaAliasEnrichmentSettings:
    environ = os.environ if environ is None else environ
    enabled = _parse_bool(environ.get("MEDIA_ALIAS_ENRICHMENT_ENABLED", "false"))
    if not enabled:
        return MediaAliasEnrichmentSettings()

    base_url = _required(environ=environ, name="OLLAMA_BASE_URL")
    model = _required(environ=environ, name="OLLAMA_MODEL")
    prompt = await _load_prompt(environ)
    settings = MediaAliasEnrichmentSettings(
        enabled=True,
        ollama_base_url=_normalize_base_url(base_url),
        ollama_model=model,
        ollama_api_key=environ.get("OLLAMA_API_KEY", "").strip() or None,
        ollama_request_timeout=_seconds(environ=environ, name="OLLAMA_REQUEST_TIMEOUT_SECONDS", default=120),
        prompt=prompt,
        concurrency=_positive_int(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_CONCURRENCY", default=2),
        poll_interval=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_POLL_INTERVAL_SECONDS", default=5),
        lease_ttl=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_LEASE_TTL_SECONDS", default=300),
        lease_refresh=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH_SECONDS", default=60),
        max_attempts=_positive_int(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_MAX_ATTEMPTS", default=5),
        retry_base=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_RETRY_BASE_SECONDS", default=30),
        retry_max=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_RETRY_MAX_SECONDS", default=3600),
        download_timeout=_seconds(environ=environ, name="MEDIA_ALIAS_ENRICHMENT_DOWNLOAD_TIMEOUT_SECONDS", default=60),
        max_source_bytes=_positive_int(
            environ=environ, name="MEDIA_ALIAS_ENRICHMENT_MAX_SOURCE_BYTES", default=20 * 1024 * 1024
        ),
        max_frame_pixels=_positive_int(
            environ=environ, name="MEDIA_ALIAS_ENRICHMENT_MAX_FRAME_PIXELS", default=40_000_000
        ),
        max_image_edge_pixels=_positive_int(
            environ=environ, name="MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_EDGE_PIXELS", default=1280
        ),
        max_image_bytes=_positive_int(
            environ=environ, name="MEDIA_ALIAS_ENRICHMENT_MAX_IMAGE_BYTES", default=4 * 1024 * 1024
        ),
    )
    if settings.lease_refresh >= settings.lease_ttl:
        raise ValueError("MEDIA_ALIAS_ENRICHMENT_LEASE_REFRESH_SECONDS must be less than lease TTL")
    if settings.retry_max < settings.retry_base:
        raise ValueError("MEDIA_ALIAS_ENRICHMENT_RETRY_MAX_SECONDS must be greater than or equal to retry base")

    return settings


async def _load_prompt(environ: Mapping[str, str]) -> str:
    prompt = environ.get("MEDIA_ALIAS_LLM_PROMPT", "").strip()
    prompt_file = environ.get("MEDIA_ALIAS_LLM_PROMPT_FILE", "").strip()
    if prompt and prompt_file:
        raise ValueError("Set only one of MEDIA_ALIAS_LLM_PROMPT or MEDIA_ALIAS_LLM_PROMPT_FILE")
    if prompt:
        return prompt
    if not prompt_file:
        raise ValueError("MEDIA_ALIAS_LLM_PROMPT or MEDIA_ALIAS_LLM_PROMPT_FILE is required when enrichment is enabled")

    return await asyncio.to_thread(_read_prompt_file, prompt_file)


def _read_prompt_file(path: str) -> str:
    try:
        if not Path(path).is_file():
            raise ValueError("MEDIA_ALIAS_LLM_PROMPT_FILE must point to an existing regular file")

        with open(path, "rb") as source:
            content = source.read(MAX_MEDIA_ALIAS_LLM_PROMPT_FILE_BYTES + 1)
    except OSError as error:
        raise ValueError(f"Cannot read MEDIA_ALIAS_LLM_PROMPT_FILE: {type(error).__name__}") from None

    if len(content) > MAX_MEDIA_ALIAS_LLM_PROMPT_FILE_BYTES:
        raise ValueError("MEDIA_ALIAS_LLM_PROMPT_FILE exceeds the 64 KiB limit")

    try:
        prompt = content.decode("utf-8-sig").strip()
    except UnicodeDecodeError:
        raise ValueError("MEDIA_ALIAS_LLM_PROMPT_FILE must contain UTF-8 text") from None

    if not prompt:
        raise ValueError("MEDIA_ALIAS_LLM_PROMPT_FILE must contain a non-empty prompt")

    return prompt


def _parse_bool(value: str) -> bool:
    match value.strip().casefold():
        case "true" | "1" | "yes" | "on":
            return True
        case "false" | "0" | "no" | "off" | "":
            return False
        case _:
            raise ValueError("MEDIA_ALIAS_ENRICHMENT_ENABLED must be a boolean")


def _required(*, environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when media alias enrichment is enabled")

    return value


def _positive_int(*, environ: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error

    if value <= 0:
        raise ValueError(f"{name} must be positive")

    return value


def _seconds(*, environ: Mapping[str, str], name: str, default: float) -> timedelta:
    try:
        value = float(environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error

    if not isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive")

    return timedelta(seconds=value)


def _normalize_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("OLLAMA_BASE_URL must be an HTTP(S) root URL without credentials, query or fragment")

    path = parsed.path.rstrip("/")
    if path:
        raise ValueError("OLLAMA_BASE_URL must be a root URL without a path")

    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
