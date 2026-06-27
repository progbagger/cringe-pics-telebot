import logging
from asyncio import subprocess
from pathlib import Path

import dotenv
import pytest

logger = logging.getLogger(__file__)
logger.setLevel(logging.DEBUG)


def get_dotenv(path: str | Path | None = None) -> dict[str, str | None]:
    return dotenv.dotenv_values(path)


@pytest.fixture(scope="session", autouse=True)
async def run_docker_compose():
    logger.info("Starting docker compose dependencies")
    process = await subprocess.create_subprocess_exec(
        "docker",
        "compose",
        "-f",
        "compose.yml",
        "-f",
        "tests/functional/compose.functional-tests.yml",
        "up",
        "-d",
    )
    exit_code = await process.wait()
    if exit_code != 0:
        _, stderr_bytes = await process.communicate()
        logger.error(
            "Can't run docker dependencies; code %s, stderr: %s",
            exit_code,
            decoded_stderr := stderr_bytes.decode(),
        )
        raise RuntimeError(f"Can't run docker dependencies; code {exit_code}, stderr: {decoded_stderr}")
    logger.info("Started docker compose dependencies")

    yield

    logger.info("Stopping docker compose dependencies")
    process = await subprocess.create_subprocess_exec("docker", "compose", "down")
    exit_code = await process.wait()
    if exit_code != 0:
        _, stderr_bytes = await process.communicate()
        logger.error("Can't stop docker dependencies; code %s, stderr: %s", exit_code, stderr_bytes.decode())

    else:
        logger.info("Stopped docker compose dependencies")
