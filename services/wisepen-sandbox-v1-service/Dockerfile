FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY services/wisepen-common/ services/wisepen-common/
COPY services/wisepen-sandbox-v1-service/ services/wisepen-sandbox-v1-service/

RUN pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple pip -U \
    && pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -e services/wisepen-common \
    && pip install --no-cache-dir -i https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple -e services/wisepen-sandbox-v1-service

EXPOSE 19915

CMD ["uvicorn", "sandbox_v1.main:app", "--host", "0.0.0.0", "--port", "19915"]
