FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS builder

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv sync --frozen --no-dev --no-editable

FROM python:3.14-slim@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH"
ENV HA_TRANSPORT=http
ENV HA_HTTP_PORT=8100

EXPOSE 8100

# Use the console script entry point. Do NOT use `python -m homeassistant_mcp.server`:
# that loads server.py as __main__, and the tools' `from homeassistant_mcp.server
# import mcp` then loads it again under its real name, creating a second FastMCP
# instance — the served instance ends up with zero tools registered.
CMD ["homeassistant-mcp"]
