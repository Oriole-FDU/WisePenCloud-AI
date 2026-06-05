# wisepen-aio-gateway

AIO Sandbox 的 Sidecar Gateway，为多用户/多会话提供安全代理层。所有请求通过 PathTranslator 实现租户级文件隔离，防止跨用户越权访问。

## 架构

```
Chat Service (Python)
    │
    │  POST /v1/aio/file/read  ──→  Gateway (FastAPI :8001)  ──→  AIO Sandbox (Docker :8080)
    │  X-From-Source / X-User-Id / X-Session-Id                 │
    │                                                           ├─ SecurityHeaderMiddleware
    │                                                           ├─ PathTranslator (租户隔离)
    │                                                           ├─ R<T> 响应包装
    │                                                           └─ WorkspaceCleaner (7天清理)
```

## 目录结构

```
src/aio_gateway/
├── main.py          # FastAPI 应用入口 + lifespan（Nacos注册/清理任务）
├── bootstrap.py     # GatewayBootstrapSettings（服务身份）
├── settings.py      # AppSettings（业务配置 + load_settings）
├── nacos.py         # NacosClientManager 实例
├── isolation.py     # TenantScope + PathTranslator（路径翻译 + 越权防御）
├── cleanup.py       # WorkspaceCleaner（基于 .last_access 的 7 天清理）
└── api/
    ├── router.py    # 路由聚合
    ├── deps.py      # get_path_translator 依赖注入
    ├── health.py    # GET  /v1/aio/health
    ├── file.py      # POST /v1/aio/file/{read|write|list|grep|replace}
    └── shell.py     # POST /v1/aio/shell/exec
```

## 配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `FROM_SOURCE_SECRET` | (必填) | 网关绕过防御 Token，与 APISIX 约定 |
| `AIO_BASE_URL` | `http://127.0.0.1:8080` | AIO Sandbox 容器地址 |
| `WORKSPACE_CLEANUP_TTL_SECONDS` | `604800` | 工作域过期时间（7天） |
| `WORKSPACE_CLEANUP_INTERVAL_SECONDS` | `3600` | 清理扫描间隔（1小时） |

DEV 模式从 `wisepen-aio-gateway.nacos.yaml` 加载，生产从 Nacos 拉取。

## API

所有接口需要 `X-From-Source` 安全 header。多租户接口额外需要 `X-User-Id` + `X-Session-Id`。

### Health

```http
GET /v1/aio/health
X-From-Source: {secret}

→ {"code":200, "data":{"status":"ok", "service":"wisepen-aio-service"}}
```

### File

LLM 视角的 workspace 根为 `/workspace/`，Gateway 自动映射到 AIO 内对应租户的物理路径。

| 端点 | 说明 |
|------|------|
| `POST /v1/aio/file/read` | 读文件。body: `{file, max_chars?}` |
| `POST /v1/aio/file/write` | 写文件（自动创建父目录）。body: `{file, content}` |
| `POST /v1/aio/file/list` | 列目录。body: `{path, recursive?}` |
| `POST /v1/aio/file/grep` | 正则搜索。body: `{path, pattern, recursive?, ignore_case?}` |
| `POST /v1/aio/file/replace` | 精确替换（Aider 风格）。body: `{file, old_str, new_str}` |

### Shell

```http
POST /v1/aio/shell/exec
X-From-Source: {secret}
{"command": "ls -la", "exec_dir": "/workspace"}

→ {"code":200, "data":{"exit_code":0, "stdout":"...", "stderr":"...", "duration_ms": 123}}
```

### Admin

```http
POST /v1/aio/admin/cleanup    # 手动触发过期工作域清理
```

## 路径隔离规则

| LLM 传入 | 物理映射 | 备注 |
|----------|---------|------|
| `/workspace/main.py` | `/home/gem/workspaces/{uid}/{sid}/main.py` | 标准虚拟路径 |
| `~/main.py` | 同上 | 简写 |
| `main.py` | 同上 | 相对路径 |
| `/etc/passwd` | 拒绝 403 | 绝对路径（不在 /workspace 下） |
| `../other/file` | 拒绝 403 | 路径穿越 |

## 测试

### 前置条件

```bash
# 1. 启动 AIO 容器
docker run -d --name aio-sandbox \
  --security-opt seccomp=unconfined \
  -p 8080:8080 \
  ghcr.io/agent-infra/sandbox:latest

# 2. 等待就绪（约 10 秒）
sleep 10
```

### 启动 Gateway

```bash
cd AI/services/wisepen-aio-gateway
uv run uvicorn aio_gateway.main:app --host 127.0.0.1 --port 8001
```

### 测试命令（Git Bash）

```bash
GW="http://127.0.0.1:8001"
SEC="local-dev-secret"

# 健康检查
curl -s "$GW/v1/aio/health" -H "X-From-Source:$SEC"

# 写文件
curl -s -X POST "$GW/v1/aio/file/write" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u1" -H "X-Session-Id:s1" \
  -d '{"file":"/workspace/hello.txt","content":"Hello AIO"}'

# 读文件
curl -s -X POST "$GW/v1/aio/file/read" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u1" -H "X-Session-Id:s1" \
  -d '{"file":"/workspace/hello.txt"}'

# 列目录
curl -s -X POST "$GW/v1/aio/file/list" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u1" -H "X-Session-Id:s1" \
  -d '{"path":"/workspace"}'

# Shell 执行
curl -s -X POST "$GW/v1/aio/shell/exec" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u1" -H "X-Session-Id:s1" \
  -d '{"command":"echo hello","exec_dir":"/workspace"}'

# 隔离验证 — u2 读不到 u1 的文件
curl -s -X POST "$GW/v1/aio/file/read" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u2" -H "X-Session-Id:s2" \
  -d '{"file":"/workspace/hello.txt"}'
# → code:500（文件不存在，因为映射到 u2 自己的空 workspace）

# 路径穿越攻击 — 拒绝
curl -s -X POST "$GW/v1/aio/file/read" \
  -H "Content-Type:application/json" -H "X-From-Source:$SEC" \
  -H "X-User-Id:u1" -H "X-Session-Id:s1" \
  -d '{"file":"/workspace/../../../etc/passwd"}'
# → code:403 "path traversal denied"
```

### 测试命令（PowerShell）

```powershell
$GW="http://127.0.0.1:8001"; $SEC="local-dev-secret"
$h=@{"X-From-Source"=$SEC; "X-User-Id"="u1"; "X-Session-Id"="s1"}

# Health
Invoke-RestMethod "$GW/v1/aio/health" -Headers @{"X-From-Source"=$SEC}

# Write
Invoke-RestMethod "$GW/v1/aio/file/write" -Method Post -ContentType "application/json" -Headers $h -Body '{"file":"/workspace/hello.txt","content":"Hello"}'

# Read
Invoke-RestMethod "$GW/v1/aio/file/read" -Method Post -ContentType "application/json" -Headers $h -Body '{"file":"/workspace/hello.txt"}'

# Shell
Invoke-RestMethod "$GW/v1/aio/shell/exec" -Method Post -ContentType "application/json" -Headers $h -Body '{"command":"echo hello","exec_dir":"/workspace"}'
```

### 清理

```bash
docker rm -f aio-sandbox
```
