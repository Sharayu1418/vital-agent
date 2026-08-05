FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN uv pip install --system --no-cache ".[postgres]"


FROM python:3.11-slim

RUN useradd --create-home appuser

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app/src ./src
RUN chmod -R a+rX /app/src

USER appuser

CMD ["uvicorn", "vital.api:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "src"]
