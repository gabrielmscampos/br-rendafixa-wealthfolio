# REST API serving br_fixed_income quotes as a Wealthfolio custom market data
# source. See the "REST API" section of the README.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Dependencies first, for layer caching. The project itself is not installed:
# its version comes from git tags (setuptools-scm), absent from the build
# context; the packages run from /app instead.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-default-groups --group rest_api

COPY br_fixed_income ./br_fixed_income
COPY rest_api ./rest_api

RUN useradd --system --uid 10001 --no-create-home app \
    && mkdir -p /data/quotes \
    && chown -R app /data
USER app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    BONDS_FILE=/data/bonds.yaml \
    OUTPUT_DIR=/data/quotes \
    XDG_CACHE_HOME=/tmp/cache

EXPOSE 8000
HEALTHCHECK --interval=1m --timeout=5s --start-period=30s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f\"http://127.0.0.1:{os.environ['PORT']}/health\", timeout=4)"]

CMD ["python", "-m", "rest_api"]
