下面这份可以直接复制成 Markdown 文档，比如：

```text
docs/docker-cheatsheet.md
```

***

# Docker 常用命令速查

## 0. 本项目约定

本项目 Docker 相关文件：

```text
docker-compose-build.yml
docker-compose-app.yml
Dockerfile
```

构建时需要同时带上：

```powershell
-f docker-compose-app.yml `
-f docker-compose-build.yml
```

不要只用 `docker-compose-app.yml` 构建，否则可能出现：

```text
No services to build
```

***

# 1. Compose 构建与启动

## 构建 chat-service

```powershell
docker compose --progress=plain `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  build chat-service
```

## 强制重新构建 chat-service

一般不要频繁用，除非依赖层、Dockerfile、`pyproject.toml`、`uv.lock` 改了。

```powershell
docker compose --progress=plain `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  build chat-service --no-cache
```

## 本地开发：源码挂载 + 自动 reload

日常改 Python 源码用这个，不需要每次重新构建镜像：

```powershell
.\deploy-local.ps1
```

它会叠加 `docker-compose-dev.yml`，把本地源码挂载到容器：

```text
./services/wisepen-chat-service/src -> /app/services/wisepen-chat-service/src
./services/wisepen-common/src       -> /app/services/wisepen-common/src
```

并使用 `uvicorn --reload`。普通 `.py` 小改动会自动同步并触发重载。

## 首次启动或依赖变更：构建后进入开发模式

改了 `pyproject.toml`、`uv.lock`、`Dockerfile` 或系统依赖时才需要构建：

```powershell
.\deploy-local.ps1 -Build
```

如果本地内测不需要 `translation_assist` 的 OPUS-MT 翻译模型，可以跳过翻译模型预热：

```powershell
.\deploy-local.ps1 -Build -SkipTranslationPreload
```

## 生产镜像模式：启动全部服务

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  up -d --build --force-recreate
```

## 生产镜像模式：只启动 / 重建 chat-service

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  up -d --build --force-recreate chat-service
```

## 停止全部服务

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  down
```

## 停止并清理孤儿容器

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  down --remove-orphans
```

***

# 2. 查看服务状态

## 查看 Compose 服务状态

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  ps
```

## 查看所有运行中的容器

```powershell
docker ps
```

## 查看所有容器，包括已退出

```powershell
docker ps -a
```

## 查看某个容器状态和重启次数

```powershell
docker inspect wisepen-chat-service --format "Status={{.State.Status}} RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}"
```

***

# 3. 查看日志

## 只看 chat-service 实时日志

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  logs -f --tail=200 chat-service
```

## 直接用容器名看日志

```powershell
docker logs -f --tail=200 wisepen-chat-service
```

## 带时间戳查看日志

```powershell
docker logs -f --tail=200 -t wisepen-chat-service
```

## 只看最近 5 分钟日志

```powershell
docker logs --since 5m wisepen-chat-service
```

## 过滤错误日志

```powershell
docker logs -f --tail=300 wisepen-chat-service 2>&1 |
  Select-String -Pattern "ERROR|Exception|Traceback|failed|timeout|Nacos|Mongo|Redis|Qdrant|Kafka|spacy|mem0"
```

## 查看全部服务实时日志

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  logs -f --tail=200
```

退出实时日志：

```text
Ctrl + C
```

***

# 4. 进入容器

## 进入 chat-service 容器 shell

```powershell
docker exec -it wisepen-chat-service sh
```

## 在容器内执行一次命令

```powershell
docker exec wisepen-chat-service sh -lc "pwd && ls"
```

## 查看容器环境变量

```powershell
docker exec wisepen-chat-service sh -lc 'env | sort'
```

## 查看关键环境变量

```powershell
docker exec wisepen-chat-service sh -lc 'echo PROFILE=$PROFILE; echo NACOS_SERVER_ADDR=$NACOS_SERVER_ADDR; echo NACOS_USERNAME=$NACOS_USERNAME; echo NACOS_PASSWORD_LEN=${#NACOS_PASSWORD}'
```

***

# 5. 网络排查

## 查看 Docker 网络

```powershell
docker network ls
```

## 查看 wisepen-net 网络详情

```powershell
docker network inspect wisepen-net
```

## 创建 wisepen-net 网络

```powershell
docker network create wisepen-net
```

如果网络已存在，会报错，可以忽略。

## 容器内测试 DNS

```powershell
docker exec wisepen-chat-service sh -lc "getent hosts wisepen-dev-server"
```

## 容器内测试端口连通性

```powershell
docker exec wisepen-chat-service sh -lc "python - <<'PY'
import socket

targets = [
    ('wisepen-dev-server', 8848, 'Nacos'),
    ('wisepen-dev-server', 27017, 'MongoDB'),
    ('wisepen-dev-server', 6379, 'Redis'),
    ('wisepen-dev-server', 6333, 'Qdrant'),
    ('wisepen-dev-server', 9094, 'Kafka'),
]

for host, port, name in targets:
    print(f'=== {name} {host}:{port} ===')
    try:
        s = socket.create_connection((host, port), timeout=5)
        print('tcp connected')
        s.close()
    except Exception as e:
        print(type(e).__name__, e)
PY"
```

## Windows 宿主机测试端口

```powershell
Test-NetConnection 10.176.44.11 -Port 8848
Test-NetConnection 10.176.44.11 -Port 27017
Test-NetConnection 10.176.44.11 -Port 6379
Test-NetConnection 10.176.44.11 -Port 6333
Test-NetConnection 10.176.44.11 -Port 9094
```

***

# 6. 镜像管理

## 查看镜像

```powershell
docker images
```

## 查看指定镜像

```powershell
docker images local/wisepencloud-chat
docker images python
docker images ghcr.io/astral-sh/uv
```

## 手动拉基础镜像

```powershell
docker pull python:3.11-slim-bookworm
docker pull ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim
```

## 删除指定镜像

```powershell
docker rmi local/wisepencloud-chat:dev
```

如果镜像被容器占用，需要先停止并删除容器。

***

# 7. 容器管理

## 停止容器

```powershell
docker stop wisepen-chat-service
```

## 启动容器

```powershell
docker start wisepen-chat-service
```

## 重启容器

```powershell
docker restart wisepen-chat-service
```

## 删除容器

```powershell
docker rm wisepen-chat-service
```

## 强制删除容器

```powershell
docker rm -f wisepen-chat-service
```

## 删除 Compose 中的 chat-service 容器

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  rm -sf chat-service
```

***

# 8. 文件复制与热修

只改 Python 业务代码时，不一定要重新 build，可以先热修验证。

## 从宿主机复制文件到容器

```powershell
docker cp `
  .\services\wisepen-chat-service\src\chat\application\tools\reasoning\math_compute_tool.py `
  wisepen-chat-service:/app/services/wisepen-chat-service/src/chat/application/tools/reasoning/math_compute_tool.py
```

## 在容器内检查 Python 语法

```powershell
docker exec wisepen-chat-service sh -lc "/app/.venv/bin/python -m py_compile /app/services/wisepen-chat-service/src/chat/application/tools/reasoning/math_compute_tool.py"
```

## 重启容器使热修生效

```powershell
docker restart wisepen-chat-service
```

## 查看日志

```powershell
docker logs -f --tail=200 wisepen-chat-service
```

注意：热修只影响当前容器，重新 build 后会丢。最终仍然要把代码修回仓库。

***

# 9. Python / 依赖检查

## 使用容器内项目 Python

```powershell
docker exec wisepen-chat-service sh -lc "/app/.venv/bin/python --version"
```

## 检查 pip

```powershell
docker exec wisepen-chat-service sh -lc "/app/.venv/bin/python -m pip --version"
```

## 检查 spaCy 模型

```powershell
docker exec wisepen-chat-service sh -lc "/app/.venv/bin/python - <<'PY'
import spacy

spacy.load('en_core_web_sm')
print('ok')
PY"
```

## 临时安装 spaCy 模型

```powershell
docker exec wisepen-chat-service sh -lc "/app/.venv/bin/python -m pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
```

注意：临时安装在容器内，重建镜像后会丢。要长期生效，应写入 Dockerfile 或依赖配置。

***

# 10. Nacos 排查

## 查看 Nacos 环境变量

```powershell
docker exec wisepen-chat-service sh -lc 'echo PROFILE=$PROFILE; echo NACOS_SERVER_ADDR=$NACOS_SERVER_ADDR; echo NACOS_USERNAME=$NACOS_USERNAME; echo NACOS_PASSWORD_LEN=${#NACOS_PASSWORD}; echo NACOS_NAMESPACE_ID=$NACOS_NAMESPACE_ID; echo NACOS_GROUP=$NACOS_GROUP'
```

## 测试 Nacos 登录

```powershell
docker exec wisepen-chat-service sh -lc 'curl -sS -w "\nHTTP=%{http_code}\n" -X POST "http://10.176.44.11:8848/nacos/v1/auth/users/login" --data-urlencode "username=${NACOS_USERNAME}" --data-urlencode "password=${NACOS_PASSWORD}"'
```

## 硬编码测试 Nacos 登录

```powershell
docker exec wisepen-chat-service sh -lc 'curl -sS -w "\nHTTP=%{http_code}\n" -X POST "http://10.176.44.11:8848/nacos/v1/auth/users/login" -d "username=nacos&password=你的密码"'
```

***

# 11. 清理磁盘空间

## 查看 Docker 空间占用

```powershell
docker system df
```

详细查看：

```powershell
docker system df -v
```

## 清理构建缓存

比较安全：

```powershell
docker builder prune -f
```

更彻底：

```powershell
docker builder prune -a -f
```

## 清理悬空镜像

```powershell
docker image prune -f
```

## 清理所有未使用镜像

谨慎使用：

```powershell
docker image prune -a -f
```

## 清理停止容器、未使用网络、未使用镜像、构建缓存

谨慎使用：

```powershell
docker system prune -a -f
```

## 不建议轻易使用

```powershell
docker system prune -a --volumes
```

这会删除 volume，可能删掉数据库、日志、缓存等持久化数据。

***

# 12. Windows / Docker Desktop 常用处理

## 关闭 WSL

```powershell
wsl --shutdown
```

常用于 Docker Desktop 卡住、网络异常、磁盘释放不及时等情况。

## Docker Hub 拉取失败时

先单独拉基础镜像：

```powershell
docker pull python:3.11-slim-bookworm
docker pull ghcr.io/astral-sh/uv:0.6-python3.11-bookworm-slim
```

如果报 Docker Hub token / EOF：

```powershell
docker logout
docker login
docker pull python:3.11-slim-bookworm
```

仍失败时，检查 Docker Desktop 代理设置或重启 Docker Desktop。

***

# 13. 构建策略建议

## 普通 Python 代码修改

优先：

```powershell
docker cp 本地文件 容器路径
docker restart wisepen-chat-service
```

或正常 build：

```powershell
docker compose --progress=plain `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  build chat-service
```

不要默认使用：

```powershell
--no-cache
```

## 依赖变化

这些情况需要重新 build：

```text
pyproject.toml 改了
uv.lock 改了
Dockerfile 改了
系统依赖改了
新增 Python / Node / 模型依赖
```

## 完全重建

只有缓存坏了或依赖层必须重跑时才用：

```powershell
docker compose --progress=plain `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  build chat-service --no-cache
```

***

# 14. 当前项目最常用命令组合

## 本地开发启动 / 同步 chat-service 源码

日常改 Python 源码用这个，容器会挂载本地 `src` 并自动 reload：

```powershell
.\deploy-local.ps1
```

## 依赖变更后重建 chat-service

```powershell
.\deploy-local.ps1 -Build
```

本地不需要翻译工具时可以跳过 OPUS-MT 模型预热：

```powershell
.\deploy-local.ps1 -Build -SkipTranslationPreload
```

## 生产镜像模式启动 chat-service

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  up -d --build --force-recreate chat-service
```

## 查看 chat-service 日志

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  logs -f --tail=200 chat-service
```

## 查看状态

```powershell
docker compose `
  -f docker-compose-app.yml `
  -f docker-compose-build.yml `
  ps
```

## 清理构建缓存

```powershell
docker builder prune -a -f
```

## 查看空间占用

```powershell
docker system df
```
