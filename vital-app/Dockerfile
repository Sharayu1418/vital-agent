# Multi-stage: build deps once, ship a slim runtime layer
FROM python:3.11-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN uv pip install --system --no-cache .

FROM python:3.11-slim
# Run as non-root (Cloud Run doesn't require it; security reviews do)
RUN useradd --create-home appuser
USER appuser
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY src ./src
ENV PORT=8080
CMD ["uvicorn", "vital.api:app", "--host", "0.0.0.0", "--port", "8080", "--app-dir", "src"]
