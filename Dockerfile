FROM ghcr.io/astral-sh/uv:python3.14-alpine

COPY \
  # what
  pyproject.toml \
  README.md \
  # where
  /app/
COPY src /app/src

WORKDIR /app/
RUN uv sync

ENTRYPOINT [ "uv", "run", "bot"]
