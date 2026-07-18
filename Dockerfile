FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN apt-get update \
    && apt-get install --yes --no-install-recommends build-essential libpq-dev \
    && pip install --no-cache-dir "uv==0.10.12" \
    && uv sync --frozen --no-dev --no-install-project \
    && rm -rf /root/.cache/uv /var/lib/apt/lists/*

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 yield-agent \
    && useradd --system --uid 10001 --gid yield-agent --home-dir /nonexistent yield-agent

COPY --from=builder /app/.venv /app/.venv
COPY --chown=yield-agent:yield-agent 08-YieldAgent ./08-YieldAgent

WORKDIR /app/08-YieldAgent
USER 10001:10001

EXPOSE 8000
CMD ["uvicorn", "agent_server:app", "--host", "0.0.0.0", "--port", "8000"]
