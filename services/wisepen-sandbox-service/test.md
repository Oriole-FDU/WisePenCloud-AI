# wisepen-sandbox-service 测试指引

本文用于当前绕过 Nacos 的本地测试分支。测试分为 pytest 自动测试和真实系统测试两部分。真实系统测试按 README 泳道图验证预热、租约、工具执行、工作区缓存、销毁和补池。

## 1. 请求约定

Sandbox Service 启用了 SecurityHeaderMiddleware。请求缺少来源头时会统一返回 404 Not Found，不会进入 FastAPI 路由。

本地默认来源密钥为 APISIX-wX0iR6tY。以下所有 HTTP 请求都必须增加：

~~~text
X-From-Source: APISIX-wX0iR6tY
~~~

业务错误通常仍使用 HTTP 200，必须检查响应 JSON 的 code、msg 和 data。缺少来源头时的 HTTP 404 是安全中间件的拒绝，不是路由不存在。

## 2. 自动测试

从服务目录执行：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-sandbox-service
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q
~~~

当前分支包含 Sandbox 生命周期、MCP 和 VNC 测试；使用仓库 `.venv` 执行。分组执行：

~~~bash
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_lifecycle.py
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_contracts.py
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_aio_adapter.py
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_api.py
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_mcp_transport.py tests/test_mcp_session.py
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q tests/test_vnc_binding.py
~~~

如果 uv workspace 环境已经准备好，也可以使用：

~~~bash
uv run --no-sync pytest -q
~~~

覆盖范围：

| 测试文件 | 主要内容 |
| --- | --- |
| test_lifecycle.py | 并发 checkout、allocate/execute/release、过期租约恢复、缓存提交、Watcher 补池 |
| test_contracts.py | request_id 幂等、fencing、状态机、路径安全、缓存替换、缓存上限、metrics |
| test_aio_adapter.py | AIO HTTP 路径和字段、错误码、路径隔离、Docker TTY 和测试标签 |
| test_api.py | FastAPI 路由、内部协议响应、fencing 错误、metrics |
| test_mcp_transport.py | MCP 工具注册、租约控制、Streamable HTTP 往返和代码执行契约 |
| test_mcp_session.py | request ID header 透传、租约复用和 MCP release 上下文 |
| test_vnc_binding.py | VNC 绑定并发幂等、释放幂等 |

pytest 的 API 测试使用 httpx.ASGITransport 直接调用 FastAPI App，不经过 uvicorn 和安全中间件，因此自动测试不需要来源头；真实 curl 必须带来源头。

## 3. 本地启动

当前分支绕过 Nacos，显式关闭 Nacos：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-sandbox-service

NACOS_ENABLED=false \
SANDBOX_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest \
SANDBOX_BROWSER_NO_SANDBOX=--no-sandbox \
PYTHONPATH=src:../wisepen-common/src \
../../.venv/bin/python -m uvicorn sandbox.main:app \
  --host 127.0.0.1 \
  --port 19905
~~~

启动日志应包含 Nacos 已关闭、跳过服务注册、Application startup complete 和 Uvicorn running on http://127.0.0.1:19905。

确认实际加载的源码和路由：

~~~bash
NACOS_ENABLED=false \
PYTHONPATH=src:../wisepen-common/src \
../../.venv/bin/python -c '
import sandbox.main
print(sandbox.main.__file__)
print([getattr(route, "path", None) for route in sandbox.main.app.routes])
'
~~~

输出应包含：

~~~text
/healthz
/readyz
/internal/pool/metrics
/internal/sandboxes/allocate
/mcp
/v1/sandbox/gateway/vnc
~~~

## 4. 健康、就绪和 metrics

~~~bash
curl -i -H 'X-From-Source: APISIX-wX0iR6tY' http://127.0.0.1:19905/healthz

curl -i -H 'X-From-Source: APISIX-wX0iR6tY' http://127.0.0.1:19905/readyz

curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/internal/pool/metrics

curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/openapi.json
~~~

预期：

- /healthz 返回 HTTP 200 和 {"status":"ok"}；
- /readyz 在 READY 不足时返回 HTTP 503，预热完成后返回 HTTP 200；
- metrics 中有 generation、readiness 和生命周期计数；
- OpenAPI JSON 中存在 /healthz、/readyz 和 /internal/* 路径。

检查预热容器：

~~~bash
docker ps --filter label=wisepen.managed=true
docker ps --filter label=wisepen.e2e=true
~~~

预热状态应经过：

~~~text
CREATING -> WARMING -> READY
~~~

## 5. allocate、execute、release

### 5.0 与 VNC 合并执行脚本

如果要一次验证第 5 章租约/工具流程和第 9 章 VNC 复用，使用下面两段脚本。脚本默认使用 tenant-a、workspace-a 和 mcp:tenant-a:workspace-a，并把租约信息保存到 /tmp/wisepen-sandbox-e2e-lease.json，不需要手工替换 lease_id 或 fencing_token。

先执行租约脚本。它会等待 /readyz，完成 allocate、幂等 allocate、write/read、Shell、代码执行和错误 fencing 校验，然后故意保留 RUNNING 租约供 VNC 使用：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-sandbox-service

../../.venv/bin/python scripts/run_lease_case.py
~~~

再执行 VNC 脚本。它会请求 VNC 302，打印真实 noVNC URL，校验 tenant-a:workspace-a 绑定的 sandbox_id 与第一个脚本相同，写入两个版本的 probe.txt 并在终端暂停，最后执行 VNC release、重复 release 和旧租约失效校验：

~~~bash
../../.venv/bin/python scripts/run_vnc_case.py
~~~

脚本暂停时，在浏览器打开它打印的 URL，进入 /home/gem/tenant-a/workspace-a 查看 probe.txt。第一次暂停确认 vnc-live-001，第二次刷新目录确认 vnc-live-002 后按回车。若只想验证 HTTP 流程，可使用 --no-pause。脚本失败后仍可使用状态文件中的租约信息排查，或等待租约 TTL 回收。

### 5.1 allocate

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/sandboxes/allocate \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "mcp:tenant-a:workspace-a",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a"
  }'
~~~

从响应 data 记录 lease_id、sandbox_id、fencing_token、expires_at 和 endpoint。预期状态为：

~~~text
READY -> ALLOCATED -> RUNNING
~~~

本 case 后续要复用 VNC，因此不要改动 `tenant-a`、`workspace-a` 或 request_id。VNC 网关会用 `mcp:<X-User-Id>:<X-Session-Id>` 作为同一租约的幂等 request_id；本 case 对应的 VNC 请求头必须使用 `X-User-Id: tenant-a` 和 `X-Session-Id: workspace-a`。记录这里的 `sandbox_id`，第 9 节需要与 VNC status 返回值比对。

查询状态：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/internal/sandboxes/<SANDBOX_ID>
~~~

管理接口不应暴露 provider_id、Docker container ID 或 AIO token。

同一个 request_id、租户和工作区再次 allocate，应返回同一个 lease。同一个 request_id 改用其他 tenant_id，应返回 REQUEST_CONFLICT。

### 5.2 write_file 和 read_file

当前 execute API 的工具参数必须放在 payload 中：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-write-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": <FENCING_TOKEN>,
    "operation": "write_file",
    "payload": {
      "file": "probe.txt",
      "content": "cached-value"
    }
  }'

curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-read-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": <FENCING_TOKEN>,
    "operation": "read_file",
    "payload": {
      "file": "probe.txt"
    }
  }'
~~~

读取结果应包含 cached-value。

### 5.3 shell_exec 和代码执行

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-shell-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": <FENCING_TOKEN>,
    "operation": "shell_exec",
    "payload": {
      "command": "printf wisepen-e2e",
      "exec_dir": ".",
      "timeout_ms": 30000
    }
  }'

curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-code-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": <FENCING_TOKEN>,
    "operation": "execute",
    "payload": {
      "language": "python",
      "code": "print(\"wisepen-e2e\")"
    }
  }'
~~~

### 5.4 fencing 拒绝

将 token 换成错误值：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-invalid-fencing",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": 999999,
    "operation": "shell_exec",
    "payload": {
      "command": "echo should-not-run"
    }
  }'
~~~

预期 HTTP 通常为 200，但业务错误为 FENCING_REJECTED，data 为 null。

### 5.5 保持 case 租约供 VNC 使用

为了让第 9 节绑定本 case 的同一个沙箱，本节暂不执行 release。请先完成第 9 节的 VNC 打开、文件更新和 VNC release；VNC release 会通过相同的 `tenant-a/workspace-a` 会话复用并释放这个 case 租约。

执行顺序是：完成 5.1–5.4 后直接跳到第 9 节；第 9 节完成 VNC release 后，再回到第 6 节继续验证工作区缓存恢复。

如果只验证内部 release API 而不验证 VNC 复用，可以在其他独立 lease 上执行原来的 release 流程；不要在本 case 上先调用内部 release，否则 VNC 会话随后只能重新分配一个新沙箱。

## 6. 工作区缓存恢复

检查缓存：

~~~bash
find /tmp/wisepen-workspaces/tenant-a/workspace-a -maxdepth 3 -type f -print
cat /tmp/wisepen-workspaces/tenant-a/workspace-a/.wisepen-workspace-manifest.json
~~~

完成第 9 节并释放本 case lease 后应能看到 probe.txt 和 manifest。然后使用相同 tenant_id=tenant-a、workspace_id=workspace-a，但新的 request_id 再次 allocate：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/sandboxes/allocate \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "manual-002",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a"
  }'
~~~

在新 lease 中执行 read_file，应读取到上一轮的 cached-value。使用 tenant-b 或其他 workspace 读取同名文件，不能读到 tenant-a/workspace-a 的内容。

commit 是完整快照替换：下一轮只提交 new.txt 后，旧的 probe.txt 应被删除，不应永久残留。

## 7. Watcher 补池和 AIO 检查

allocate 一个 READY Sandbox 后检查：

~~~bash
docker ps --filter label=wisepen.managed=true

curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/internal/pool/metrics
~~~

预期用户实例不回 READY，Watcher 发现 READY 缺口并创建新容器：

~~~text
用户实例：ALLOCATED/RUNNING
替代容器：CREATING -> WARMING -> READY
~~~

AIO 容器的真实健康路径是：

~~~bash
curl -i http://127.0.0.1:<AIO_PORT>/v1/sandbox
~~~

预期 HTTP 200。README 中的 /health、/v1/health 和 /openapi.json 不是 AIO 健康路径。

## 8. MCP 接口

MCP 入口为：

~~~text
http://127.0.0.1:19905/mcp/
~~~

MCP Inspector 或自定义 MCP Client 必须发送：

~~~text
X-From-Source: APISIX-wX0iR6tY
X-User-Id: user-a
X-Session-Id: session-a
~~~

应能发现并调用：

~~~text
read_file
write_file
list_directory
grep_files
edit_file
shell_exec
run_sandbox_script
~~~

同一用户和会话的多个工具调用应复用同一个租约。缺少来源头时：

~~~bash
curl -i http://127.0.0.1:19905/mcp/
~~~

预期 HTTP 404；带来源头后才进入 MCP 路由。

## 9. VNC 网关

第 9 章的手工 curl 可以由第 5.0 节的 scripts/run_vnc_case.py 完成。脚本保留这里的人工检查目的：必须实际打开打印出的 noVNC URL，并观察同一个工作区文件内容变化；仅收到 302 不能证明真实 VNC E2E 通过。

本节必须在第 5.5 节保持 case 租约期间执行。这里不创建新的 `user-a/session-a` 会话，而是使用第 5.1 节 case 的 `tenant-a/workspace-a`，因此 VNC 会复用前面记录的 `CASE_SANDBOX_ID`。

查询绑定状态：

~~~bash
curl -i -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/v1/sandbox/gateway/vnc/status
~~~

建立绑定：

~~~bash
curl -i -H 'X-From-Source: APISIX-wX0iR6tY' \
  -H 'X-User-Id: tenant-a' \
  -H 'X-Session-Id: workspace-a' \
  http://127.0.0.1:19905/v1/sandbox/gateway/vnc
~~~

预期 HTTP 302，Location 类似：

~~~text
http://127.0.0.1:<AIO_PORT>/vnc/index.html?autoconnect=true
~~~

再次查询绑定状态：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  http://127.0.0.1:19905/v1/sandbox/gateway/vnc/status
~~~

`bindings` 中应出现 `tenant-a:workspace-a`，且值必须等于第 5.1 节记录的 `CASE_SANDBOX_ID`。这证明 VNC 没有重新 checkout 另一个 READY 沙箱。

在浏览器打开 Location 后，在 VNC 的文件管理器中进入：

~~~text
/home/gem/tenant-a/workspace-a
~~~

保持 VNC 页面打开，再通过同一个 `<LEASE_ID>` 更新文件：

~~~bash
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/<LEASE_ID>/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-vnc-live-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": <FENCING_TOKEN>,
    "operation": "write_file",
    "payload": {
      "file": "probe.txt",
      "content": "vnc-live-001"
    }
  }'
curl -s -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST http://127.0.0.1:19905/internal/leases/lease_4/execute \
  -H 'Content-Type: application/json' \
  -d '{
    "request_id": "tool-vnc-live-001",
    "tenant_id": "tenant-a",
    "workspace_id": "workspace-a",
    "fencing_token": 4,
    "operation": "write_file",
    "payload": {
      "file": "probe.txt",
      "content": "vnc-live-001"
    }
  }'
~~~

VNC 中刷新该目录后应看到 `probe.txt` 的内容变化；再次执行同一请求将 content 改为 `vnc-live-002`，可以验证 VNC 和 execute 访问的是同一个运行中的文件系统。这里的“实时”表示同一 AIO 容器内的文件状态实时一致，文件管理器可能需要手动刷新。

相同用户和会话重复请求应幂等。最后通过 VNC 网关释放这个共享 case lease：

~~~bash
curl -i -H 'X-From-Source: APISIX-wX0iR6tY' \
  -X POST \
  -H 'X-User-Id: tenant-a' \
  -H 'X-Session-Id: workspace-a' \
  http://127.0.0.1:19905/v1/sandbox/gateway/vnc/release
~~~

预期状态为 `RUNNING -> SYNCING -> DESTROYING -> DESTROYED`。VNC release 后再执行 `<LEASE_ID>` 的 execute 应被拒绝；当前 README 明确尚未完成真实浏览器/Proxy 端到端验证，不能仅凭 302 宣称 VNC E2E 通过。

## 10. 清理和交付判定

只查询测试标签的容器：

~~~bash
docker ps -aq --filter label=wisepen.e2e=true
~~~

清理后确认没有残留：

~~~bash
docker ps -a --filter label=wisepen.e2e=true
~~~

交付记录：

~~~text
pytest 自动测试                    PASS / FAIL
healthz（带 X-From-Source）         PASS / FAIL
readyz 和 Watcher 预热              PASS / FAIL
allocate 生命周期                   PASS / FAIL
AIO 文件读写/搜索/替换               PASS / FAIL
Shell 执行                          PASS / FAIL
Code Execute                        PASS / FAIL
fencing 拒绝                        PASS / FAIL
workspace commit                    PASS / FAIL
下次 allocate 恢复缓存               PASS / FAIL
release 幂等                        PASS / FAIL
用户实例不回 READY                   PASS / FAIL
Watcher 补池                        PASS / FAIL
MCP Streamable HTTP                  PASS / FAIL
VNC binding/status/release           PASS / FAIL
真实浏览器 VNC E2E                   NOT IMPLEMENTED
测试容器清理                        PASS / FAIL
~~~

## 11. Chat Service 启动与联调

### 11.1 Chat 启动前提

Chat Service 的 `AppSettings` 必须从 Nacos 拉取完整配置，不能像本地 Sandbox 一样仅通过 `NACOS_ENABLED=false` 启动。因此至少需要准备：

- Nacos Config：`wisepen-chat-service-dev.yaml`，group 默认是 `DEFAULT_GROUP`；
- MongoDB：用于 Chat session、model、provider 和消息持久化；
- Redis：用于热上下文和 MCP 工具发现缓存；
- Kafka：用于 token consumption 事件；
- Qdrant 和可用的 LLM Provider：用于 Chat 初始化和 ReAct 推理。

Chat 的 Nacos 配置必须包含 `MONGODB_URL`、`MONGODB_DB_NAME`、`REDIS_URL`、`KAFKA_BOOTSTRAP_SERVERS`、`QDRANT_HOST`、`QDRANT_PASSWORD`、`LLM_BASE_URL`、`LLM_API_KEY`、`DEFAULT_MODEL_ID` 以及其他 `AppSettings` 必填字段。Sandbox 相关配置建议使用：

~~~yaml
SANDBOX_SERVICE_NAME: wisepen-sandbox-service
SANDBOX_SERVICE_URL: http://127.0.0.1:19905
SANDBOX_FROM_SOURCE: APISIX-wX0iR6tY
SANDBOX_TIMEOUT_SECONDS: 30
~~

`McpServiceClient` 会优先从 Nacos Naming 发现 `wisepen-sandbox-service`；发现失败时回退到 `SANDBOX_SERVICE_URL/mcp/`。因此本地绕过 Nacos 服务注册时，Sandbox 可以保持第 3 节的 `NACOS_ENABLED=false`，而 Chat 仍需能拉取自己的 Nacos Config。

### 11.2 启动 Chat

另开终端，从 Chat 服务目录启动。`NACOS_SERVER_ADDR`、`NACOS_NAMESPACE_ID`、`NACOS_GROUP` 等 bootstrap 参数来自环境或 `.env`；下面示例使用本地 Nacos、默认 group 和 Chat 端口：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-chat-service

PYTHONPATH=src:../wisepen-common/src \
../../.venv/bin/python -m uvicorn chat.main:app \
  --host 127.0.0.1 \
  --port 19904
~~~

启动日志应包含 Beanie 初始化、Nacos 注册、Kafka producer 启动和 `service ready`。Chat 真实生命周期会连接 MongoDB、Redis、Kafka 和 Nacos；其中任何一个依赖未就绪，都不能进行完整联调。

### 11.2.1 OSS 本地缓存目录权限（macOS，无需修改代码）

如果启动日志出现：

~~~text
PermissionError: [Errno 13] Permission denied: '/private/var/oss_cache'
~~~

这是本地 OSS 缓存目录权限问题，不是 OSS 远端访问凭证问题。Chat 启动时会创建 `OSS_CACHE_DIR`；当前默认值是 `/var/oss_cache`，macOS 会将 `/var` 解析为 `/private/var`，而该目录由 root 所有，普通用户不能在其中创建 `oss_cache`。

推荐本地开发时把 Nacos 中 `wisepen-chat-service-dev.yaml` 的缓存目录改成当前用户可写的绝对路径，例如：

~~~yaml
OSS_CACHE_DIR: /private/tmp/wisepen-chat-oss-cache
~~~

然后执行一次：

~~~bash
mkdir -p /private/tmp/wisepen-chat-oss-cache
chmod 700 /private/tmp/wisepen-chat-oss-cache
~~~

Nacos 配置的 group 默认是 `DEFAULT_GROUP`。修改并发布配置后，完全停止并重新启动 Chat Service，再确认启动日志出现 `service ready`。`/private/tmp` 适合本地测试，系统重启或清理临时文件后缓存可能消失，但不会影响业务数据。

不要只把 `OSS_CACHE_DIR` 添加到 `services/wisepen-chat-service/.env`：该 `.env` 只提供连接 Nacos 前的 bootstrap 配置，`OSS_CACHE_DIR` 由 Chat 从 Nacos 业务配置加载。也不要在 Nacos 中写 `~/...` 或 `$TMPDIR/...`，这里应填写已经展开的绝对路径。

如果不希望修改 Nacos 配置，也可以为现有默认路径创建目录并把目录所有权交给当前启动用户：

~~~bash
sudo mkdir -p /private/var/oss_cache
sudo chown "$(id -un)":"$(id -gn)" /private/var/oss_cache
sudo chmod 700 /private/var/oss_cache
~~~

不要使用 `chmod 777`。目录准备好后重新启动 Chat Service；可用下面的命令确认当前用户具有写权限：

~~~bash
test -w /private/var/oss_cache && echo oss_cache_writable=PASS
~~~

### 11.3 Chat 请求头和会话准备

`SecurityHeaderMiddleware` 要求请求携带来源头，并从网关透传的 header 读取登录用户、会话和本轮 request ID：

~~~text
X-From-Source: APISIX-wX0iR6tY
X-User-Id: chat-curl-user
X-Request-Id: <CHAT_HTTP_REQUEST_ID>
~~~

本地直连时不需要调用额外的“登录”接口。`SecurityHeaderMiddleware` 在来源头通过校验后，将非空 `X-User-Id` 写入安全上下文，满足 `require_login`。因此来源密钥是本地伪造用户身份的安全边界，不能暴露到非受信环境。

`X-Session-Id` 仅在 Chat 对话和 MCP 调用时需要，且必须是属于该 `X-User-Id` 的 Mongo ObjectId。第 11.6 节的一键脚本会自动查询模型并创建临时 session；手工调用 Chat 时仍需自行提供有效 session。模型 ID 必须来自 `GET /chat/model/listAvailableModels` 返回的 active model，不能使用显示名或 Provider 侧模型名。

### 11.4 先验证 Sandbox MCP

该脚本使用 curl 完成 MCP initialize、`tools/list`、`acquire_sandbox`、`run_sandbox_script` 和 `release_sandbox`。它只验证原始 MCP 端点，不经过 Chat 的 LLM catalog：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-sandbox-service

scripts/check_sandbox_mcp.sh \
  --base-url http://127.0.0.1:19905 \
  --source APISIX-wX0iR6tY \
  --user-id mcp-curl-user \
  --session-id mcp-curl-session \
  --request-id mcp-curl-request
~~~

预期输出包含：

~~~text
mcp_initialize=PASS
mcp_tools=PASS ... acquire_sandbox ... release_sandbox ... run_sandbox_script
mcp_acquire=PASS
mcp_run_sandbox_script=PASS
mcp_release=PASS
~~~

原始 MCP `tools/list` 会包含 acquire/release，因为它们是 Chat 内部控制能力；Chat 的 Sandbox catalog 只按 `_SANDBOX_TOOL_CONFIGS` 加载七个普通业务工具，不会把 acquire/release 传给 LLM。

### 11.5 Chat SSE 联调

单独发送 Chat 请求并保存 SSE：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-chat-service

scripts/run_chat_request.sh \
  --chat-url http://127.0.0.1:19904 \
  --source APISIX-wX0iR6tY \
  --user-id chat-curl-user \
  --developer yczhou23 \
  --session-id <EXISTING_CHAT_SESSION_ID> \
  --request-id chat-curl-request-001 \
  --model-id <MODEL_ID_FROM_LIST_AVAILABLE_MODELS> \
  --provider-id <ACTIVE_PROVIDER_ID> \
  --query "请使用 run_sandbox_script 执行 Python 代码 print('chat-mcp-curl-e2e')，并返回 JSON 执行结果。" \
  | tee /tmp/wisepen-chat-sandbox.sse
~~~

SSE 至少应按以下顺序体现工具调用和最终结束：

~~~text
tool-input-start / tool-input-available: run_sandbox_script
tool-output-available: 包含 status、request_id、sandbox_id、stdout、stderr 的 JSON
finish
[DONE]
~~~

工具输入必须使用 `language: python` 和 `code`，不能再使用旧的 `package_id` 契约。Sandbox 侧会用 Chat 本轮生成并通过 MCP header 透传的 request ID 复用租约；Chat ReAct 结束、异常或取消时，coordinator 的 finally 会调用 `release_sandbox`。

### 11.6 一键编排用例

下面的 Python 编排脚本组合多个 curl 脚本：先验证 Sandbox MCP，再读取租约池 metrics，调用 Chat SSE，解析 `run_sandbox_script` 工具输入和 JSON 输出，最后再次读取 metrics 检查 Chat 响应后没有新增 RUNNING 租约：

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-sandbox-service

../../.venv/bin/python scripts/run_chat_sandbox_e2e.py \
  --sandbox-url http://127.0.0.1:19905 \
  --chat-url http://127.0.0.1:19904 \
  --source APISIX-wX0iR6tY \
  --user-id chat-curl-user \
  --developer yczhou23
~~~

脚本按 system model、当前用户 model 的顺序选择 `support_tools=true` 且有 active Provider mapping 的模型，随后创建默认 Agent 的临时 session，并将选中的 model/provider 显式传给 Chat。可用 `--model-id` 与 `--provider-id` 限定目标，或用 `--session-id` 复用已有会话；显式值仍会先在 `listAvailableModels` 响应中验证。若 Sandbox Nacos 实例带有 `developer` metadata，必须用 `--developer <该值>` 透传 `X-Developer`，让 Chat 的灰度服务发现选择该实例。

脚本结束时会删除它自行创建的 Chat session 主记录；显式传入的 session 永不删除。现有 `deleteSession` 不会级联清理聊天消息、Redis 热上下文或长期记忆，因此这些残留不在本联调用例的清理范围内。

该检查要求没有其他并发租约改变 metrics；在共享开发环境中，`running_after > running_before` 只能说明存在并发租约或释放异常，需要结合 `/internal/pool/metrics` 和服务日志复核。若 MongoDB、Redis、Kafka、Nacos、LLM、AIO 镜像或 Chat session/model 任一外部依赖不可用，脚本应报告失败，不能标记 live E2E 通过。

### 11.7 Chat 自动测试和编译检查

~~~bash
cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo/services/wisepen-chat-service
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q
PYTHONPATH=src:../wisepen-common/src ../../.venv/bin/python -m pytest -q \
  tests/test_sandbox_client.py \
  tests/test_mcp_service_client.py \
  tests/test_sandbox_mcp_catalog.py \
  tests/test_run_sandbox_script.py \
  tests/test_chat_coordinator_sandbox_lease.py

cd /Users/julius/julProg/wisepen/WisePenCloud-AI-fork-simo
../../.venv/bin/python -m compileall -q \
  services/wisepen-common/src/common \
  services/wisepen-sandbox-service/src/sandbox \
  services/wisepen-chat-service/src/chat
~~~

服务注册检查可使用：

~~~bash
rg -n 'acquire_sandbox|release_sandbox|run_sandbox_script|_SANDBOX_TOOL_CONFIGS' \
  services/wisepen-sandbox-service/src services/wisepen-chat-service/src
~~~
