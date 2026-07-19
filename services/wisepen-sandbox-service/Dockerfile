FROM python:3.11-slim

# Install Docker CLI (client only — talks to host daemon via mounted socket)
RUN apt-get update && apt-get install -y curl ca-certificates gnupg \
    && curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/docker.asc] \
       https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/debian bookworm stable" \
       > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy uv workspace
COPY pyproject.toml .
COPY services/wisepen-common/ services/wisepen-common/
COPY services/wisepen-sandbox-service/ services/wisepen-sandbox-service/

# Install Python deps (editable mode)，使用清华镜像
RUN pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple pip -U \
    && pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -e services/wisepen-common \
    && pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -e services/wisepen-sandbox-service

EXPOSE 9001 8001

# 仅启动 gateway（含文件/Shell/VNC/MCP 全部端点，统一管理容器池）
CMD ["uvicorn", "sandbox.gateway.main:app", "--host", "0.0.0.0", "--port", "8001"]
