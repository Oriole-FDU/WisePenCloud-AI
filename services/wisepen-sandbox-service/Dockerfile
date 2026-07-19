FROM python:3.11-slim

# Install Docker CLI (client only — talks to host daemon via mounted socket)
RUN apt-get update && apt-get install -y curl ca-certificates \
    && curl -fsSL https://get.docker.com -o /tmp/get-docker.sh \
    && sh /tmp/get-docker.sh \
    && rm /tmp/get-docker.sh \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy uv workspace
COPY pyproject.toml .
COPY services/wisepen-common/ services/wisepen-common/
COPY services/wisepen-sandbox-service/ services/wisepen-sandbox-service/

# Install Python deps (editable mode)
RUN pip install --no-cache-dir uv \
    && uv pip install --system -e services/wisepen-common \
    -e services/wisepen-sandbox-service

EXPOSE 9001 8001

# Start both sandbox HTTP server and gateway
CMD ["sh", "-c", "python -m sandbox.transport.http.server & python -m uvicorn sandbox.gateway.main:app --host 0.0.0.0 --port 8001"]
