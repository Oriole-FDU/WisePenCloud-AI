FROM python:3.11-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml .
COPY services/wisepen-common/ services/wisepen-common/
COPY services/wisepen-aio-gateway/ services/wisepen-aio-gateway/

RUN uv sync --no-dev --frozen

EXPOSE 8001

CMD ["uv", "run", "uvicorn", "aio_gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
