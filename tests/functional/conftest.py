import asyncio
import logging
import os
import socket
import sys
from asyncio import subprocess
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp
import asyncpg
import pytest
import pytest_asyncio
from redis import asyncio as redis

ROOT_DIR = Path(__file__).parents[2]
FUNCTIONAL_DIR = ROOT_DIR / "tests" / "functional"
COMPOSE_FILES = (
    "-f",
    str(FUNCTIONAL_DIR / "compose.functional-tests.yml"),
)
COMPOSE_PROJECT_NAME = f"cringe-pics-telebot-functional-{os.getpid()}"
POSTGRES_ENV = {
    "POSTGRES_USER": "functional",
    "POSTGRES_PASSWORD": "functional",
    "POSTGRES_DB": "cringe_pics_telebot_functional",
    "POSTGRES_HOST": "127.0.0.1",
}
REDIS_ENV = {
    "REDIS_USERNAME": "functional",
    "REDIS_PASSWORD": "functional",
    "REDIS_HOST": "127.0.0.1",
}
BOT_ENV = {
    **POSTGRES_ENV,
    **REDIS_ENV,
    "TELEGRAM_BOT_TOKEN": "123456:functional-test-token",
    "YANDEX_DISK_TOKEN": "functional-test-yandex-token",
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DependencyPorts:
    postgres: int
    redis: int


@dataclass(slots=True)
class FakeTelegramServer:
    base_url: str
    process: subprocess.Process

    async def push_message(self, *, text: str, user_id: int = 42, first_name: str = "Functional") -> dict[str, Any]:
        update = {
            "message": {
                "message_id": 1,
                "date": int(datetime(2026, 8, 11, tzinfo=UTC).timestamp()),
                "chat": {
                    "id": user_id,
                    "type": "private",
                    "first_name": first_name,
                },
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": first_name,
                    "language_code": "ru",
                },
                "text": text,
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text)}],
            }
        }
        async with (
            aiohttp.ClientSession() as session,
            session.post(f"{self.base_url}/test/updates", json=update) as response,
        ):
            response.raise_for_status()
            return await response.json()

    async def requests(self, *, method: str | None = None) -> list[dict[str, Any]]:
        params = {"method": method} if method is not None else None
        async with (
            aiohttp.ClientSession() as session,
            session.get(f"{self.base_url}/test/requests", params=params) as response,
        ):
            response.raise_for_status()
            body = await response.json()
            return body["result"]

    async def wait_for_request(
        self,
        method: str,
        *,
        predicate: Callable[[dict[str, Any]], bool] | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            _raise_if_process_exited(self.process, "fake Telegram")
            for request in await self.requests(method=method):
                if predicate is None or predicate(request):
                    return request

            await asyncio.sleep(0.1)

        raise TimeoutError(f"Telegram request {method!r} was not received in {timeout} seconds")


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def docker_compose() -> AsyncIterator[DependencyPorts]:
    dependency_ports = DependencyPorts(postgres=_get_unused_tcp_port(), redis=_get_unused_tcp_port())
    compose_env = {
        **os.environ,
        "FUNCTIONAL_POSTGRES_PORT": str(dependency_ports.postgres),
        "FUNCTIONAL_REDIS_PORT": str(dependency_ports.redis),
    }

    logger.info("Starting docker compose dependencies")
    await _run_checked(
        "docker",
        "compose",
        "-p",
        COMPOSE_PROJECT_NAME,
        *COMPOSE_FILES,
        "up",
        "-d",
        "--build",
        "postgres",
        "redis",
        env=compose_env,
    )
    await _wait_until_ready(lambda: _postgres_ready(dependency_ports), "Postgres")
    await _wait_until_ready(lambda: _redis_ready(dependency_ports), "Redis")

    try:
        yield dependency_ports
    finally:
        logger.info("Stopping docker compose dependencies")
        await _run_checked(
            "docker",
            "compose",
            "-p",
            COMPOSE_PROJECT_NAME,
            *COMPOSE_FILES,
            "down",
            "-v",
            "--remove-orphans",
            env=compose_env,
        )


@pytest.fixture
async def fake_telegram_server(unused_tcp_port: int) -> AsyncIterator[FakeTelegramServer]:
    process = await subprocess.create_subprocess_exec(
        sys.executable,
        str(FUNCTIONAL_DIR / "fake_telegram.py"),
        "--port",
        str(unused_tcp_port),
        cwd=ROOT_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    server = FakeTelegramServer(base_url=f"http://127.0.0.1:{unused_tcp_port}", process=process)
    await _wait_until_ready(lambda: _http_ready(f"{server.base_url}/healthz"), "fake Telegram")

    try:
        yield server
    finally:
        await _terminate_process(process)


@pytest.fixture
async def bot_process(
    docker_compose: DependencyPorts,
    fake_telegram_server: FakeTelegramServer,
) -> AsyncIterator[subprocess.Process]:
    env = _bot_env(docker_compose)
    env["TELEGRAM_API_BASE_URL"] = fake_telegram_server.base_url

    process = await subprocess.create_subprocess_exec(
        "uv",
        "run",
        "bot",
        cwd=ROOT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        await fake_telegram_server.wait_for_request("getMe", timeout=15)
        yield process
    finally:
        await _terminate_process(process)


def _bot_env(dependency_ports: DependencyPorts) -> dict[str, str]:
    env = os.environ.copy()
    env.update(BOT_ENV)
    env["POSTGRES_PORT"] = str(dependency_ports.postgres)
    env["REDIS_PORT"] = str(dependency_ports.redis)

    return env


async def _run_checked(*args: str, env: dict[str, str] | None = None) -> str:
    process = await subprocess.create_subprocess_exec(
        *args,
        cwd=ROOT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        command = " ".join(args)
        raise RuntimeError(
            f"Command failed with exit code {process.returncode}: {command}\n"
            f"stdout:\n{stdout.decode()}\n"
            f"stderr:\n{stderr.decode()}"
        )

    return stdout.decode()


async def _wait_until_ready(check: Callable[[], Awaitable[None]], name: str, *, timeout: float = 30) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            await check()
        except Exception as error:
            last_error = error
            await asyncio.sleep(0.25)
        else:
            return

    raise TimeoutError(f"{name} did not become ready in {timeout} seconds") from last_error


async def _postgres_ready(dependency_ports: DependencyPorts) -> None:
    connection = await asyncpg.connect(
        user=POSTGRES_ENV["POSTGRES_USER"],
        password=POSTGRES_ENV["POSTGRES_PASSWORD"],
        database=POSTGRES_ENV["POSTGRES_DB"],
        host=POSTGRES_ENV["POSTGRES_HOST"],
        port=dependency_ports.postgres,
    )
    await connection.close()


async def _redis_ready(dependency_ports: DependencyPorts) -> None:
    client = redis.from_url(
        f"redis://{REDIS_ENV['REDIS_HOST']}:{dependency_ports.redis}",
        username=REDIS_ENV["REDIS_USERNAME"],
        password=REDIS_ENV["REDIS_PASSWORD"],
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _http_ready(url: str) -> None:
    async with aiohttp.ClientSession() as session, session.get(url) as response:
        response.raise_for_status()


async def _terminate_process(process: subprocess.Process) -> None:
    if process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()

    await process.communicate()


def _raise_if_process_exited(process: subprocess.Process, name: str) -> None:
    if process.returncode is None:
        return

    raise RuntimeError(f"{name} process exited unexpectedly with code {process.returncode}")


def _get_unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
