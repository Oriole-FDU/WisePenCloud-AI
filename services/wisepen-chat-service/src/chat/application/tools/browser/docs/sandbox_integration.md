# browser_interact 沙箱接入说明

本文档面向后续接入浏览器沙箱运行时。目标是在不破坏当前本地 Playwright 链路的前提下，让 `browser_interact` 可以通过配置切换到沙箱，并继续复用已有 action、snapshot、response、intervention 能力。

## 接入原则

1. `browser_interact` 只关心浏览器能力，不直接关心沙箱容器生命周期。
2. 本地环境和沙箱环境必须通过容器注入和配置切换，禁止在 action 内写死运行形态。
3. action 层继续只处理业务动作，如 `navigate`、`click_ref`、`snapshot`、`get_content`。
4. session/runtime 层负责屏蔽本地 Playwright 与沙箱 Playwright 的差异。
5. tool 层继续统一校验 `session_id` 和 `user_id`；内部可控服务不做重复入参校验。
6. 下载不在 `browser_interact` 内接管文件内容，只保证 `click_ref(expect_download=true)` 能触发并观测下载事件；沙箱负责把下载产物同步到服务端临时文件。

## 当前可复用结构

当前 `browser_interact` 的核心边界如下：

| 模块 | 职责 | 沙箱接入影响 |
| --- | --- | --- |
| `browse_interact_tool.py` | tool schema、上下文校验、容器入口 | 增加运行时配置注入即可 |
| `controller.py` | action 分发、统一错误处理、响应组装入口 | 不应感知本地或沙箱细节 |
| `runtime/session.py` | 浏览器会话、page/context、事件观测 | 主要改造点 |
| `runtime/action_runtime.py` | ref 定位、iframe 路径、下载触发观测 | 保持复用，必要时适配下载事件对象 |
| `runtime/content.py` | HTML 到 markdown 内容提取 | 继续复用 |
| `snapshot/manager.py` | snapshot/ref 构建 | 继续复用 |
| `response/build_response.py` | response 构建 | 继续复用 |
| `enums.py` | 稳定枚举 | 增加 sandbox provider |
| `models.py` | dataclass | 增加沙箱配置 dataclass 时仍放这里 |

## 推荐运行时边界

后续不要把沙箱逻辑直接塞进 `BrowserSessionManager.get_or_create_page()`。推荐先把“创建/关闭浏览器资源”的差异抽成 runtime provider，再由 `BrowserSessionManager` 继续管理稳定会话语义。

建议新增内部协议：

```python
from typing import Protocol

from playwright.async_api import Browser, BrowserContext, Page


class BrowserRuntimeProvider(Protocol):
    """创建和释放 browser_interact 所需的浏览器运行时资源。"""

    async def start(self) -> tuple[Browser, BrowserContext, Page]:
        """启动运行时并返回 browser、context 和初始 page。"""

    async def close(self) -> None:
        """释放 provider 持有的运行时资源。"""
```

推荐文件布局：

```text
browser_interact/
  controller.py
  enums.py
  errors.py
  models.py
  runtime/
    action_runtime.py
    content.py
    intervention.py
    providers.py
    session.py
```

如果 provider 实现明显变多，再考虑：

```text
runtime/
  providers/
    base.py
    local_playwright.py
    sandbox_playwright.py
```

但在只有两个 provider 前，不要过早拆出大量薄文件。

## 配置项建议

当前已有配置：

```python
BROWSER_INTERACT_TIMEOUT_SECONDS: int = 30
BROWSER_INTERACT_HEADLESS: bool = False
BROWSER_INTERACT_DISABLE_SANDBOX: bool = False
BROWSER_INTERACT_DISABLE_DEV_SHM_USAGE: bool = False
```

建议新增：

```python
BROWSER_INTERACT_RUNTIME_PROVIDER: str = "local_playwright"
BROWSER_INTERACT_SANDBOX_ENDPOINT: str | None = None
BROWSER_INTERACT_SANDBOX_WORKSPACE_ID: str | None = None
BROWSER_INTERACT_SANDBOX_TEMP_ROOT: str | None = None
```

说明：

| 配置 | 用途 |
| --- | --- |
| `BROWSER_INTERACT_RUNTIME_PROVIDER` | `local_playwright` / `sandbox_playwright` |
| `BROWSER_INTERACT_SANDBOX_ENDPOINT` | 沙箱控制面地址或 browser ws endpoint 来源 |
| `BROWSER_INTERACT_SANDBOX_WORKSPACE_ID` | 多租户或多任务沙箱隔离标识 |
| `BROWSER_INTERACT_SANDBOX_TEMP_ROOT` | 沙箱同步到服务端的临时文件根目录 |

`BROWSER_INTERACT_DISABLE_SANDBOX` 是 Chromium 自身的 `--no-sandbox` 开关，不等同于业务沙箱 provider。后续命名上要避免混淆。

## 枚举和模型调整

`RuntimeProvider` 建议扩展：

```python
class RuntimeProvider(StrEnum):
    """浏览器运行时提供方。"""

    LOCAL_PLAYWRIGHT = "local_playwright"
    SANDBOX_PLAYWRIGHT = "sandbox_playwright"
```

`BrowserLaunchOptions` 可以继续作为统一配置载体，并增加可选沙箱字段：

```python
@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    timeout: int = 30
    headless: bool = False
    disable_sandbox: bool = False
    disable_dev_shm_usage: bool = False
    runtime_provider: str = RuntimeProvider.LOCAL_PLAYWRIGHT.value
    runtime_engine: str = BrowserEngine.CHROMIUM.value
    sandbox_endpoint: str | None = None
    sandbox_workspace_id: str | None = None
    sandbox_temp_root: str | None = None
```

模型只承载数据，不放 provider 构建、response 格式化或转换函数。

## 容器注入方式

`container.py` 中继续只负责把 `tool_settings` 注入 `BrowseInteractTool`：

```python
tool(
    "browse_interact_tool",
    BrowseInteractTool,
    timeout=tool_settings.BROWSER_INTERACT_TIMEOUT_SECONDS,
    headless=tool_settings.BROWSER_INTERACT_HEADLESS,
    disable_sandbox=tool_settings.BROWSER_INTERACT_DISABLE_SANDBOX,
    disable_dev_shm_usage=tool_settings.BROWSER_INTERACT_DISABLE_DEV_SHM_USAGE,
    runtime_provider=tool_settings.BROWSER_INTERACT_RUNTIME_PROVIDER,
    sandbox_endpoint=tool_settings.BROWSER_INTERACT_SANDBOX_ENDPOINT,
    sandbox_workspace_id=tool_settings.BROWSER_INTERACT_SANDBOX_WORKSPACE_ID,
    sandbox_temp_root=tool_settings.BROWSER_INTERACT_SANDBOX_TEMP_ROOT,
)
```

`BrowseInteractTool.__init__()` 只组装 `BrowserLaunchOptions`，不实例化沙箱客户端。沙箱客户端或 Playwright 连接细节应留在 runtime provider 内。

## 沙箱 provider 行为

`sandbox_playwright` provider 至少需要提供以下能力：

1. 创建或获取一个隔离浏览器环境。
2. 返回兼容 Playwright `Page` / `BrowserContext` 的对象。
3. 支持 `page.goto`、locator、frame、keyboard、mouse、screenshot、content、download event。
4. 能暴露下载事件的文件名、建议文件名、沙箱临时路径或服务端同步路径。
5. 支持关闭 page/context/browser，并触发沙箱资源释放或租约归还。

如果沙箱提供的是 browser websocket endpoint，provider 可以使用：

```python
self._browser = await self._playwright.chromium.connect_over_cdp(endpoint)
```

或：

```python
self._browser = await self._playwright.chromium.connect(endpoint)
```

具体使用哪种连接方式取决于沙箱暴露的是 CDP endpoint 还是 Playwright ws endpoint。

## 下载边界

当前约定：

1. `click_ref(expect_download=true)` 负责等待下载触发。
2. `browser_interact` 不保存、不上传、不解析下载文件。
3. 沙箱负责把下载内容同步到服务端临时目录。
4. response 只返回可观测元数据，如文件名、触发状态、沙箱同步路径。

建议下载 response detail 保持类似结构：

```json
{
  "download_triggered": true,
  "suggested_filename": "report.pdf",
  "sandbox_temp_path": "/tmp/wisepen/sandbox/session/report.pdf"
}
```

其中 `sandbox_temp_path` 只有沙箱 provider 能确定时才返回。本地 provider 可以只返回 `download_triggered` 和 `suggested_filename`。

## 用户介入

沙箱接入后，现有用户介入策略仍然生效：

1. 登录页、验证码页继续由 `runtime/intervention.py` 识别。
2. 高风险操作继续在 `action_policy.py` 收敛。
3. 密码、验证码、token 等敏感输入继续要求用户在浏览器内手动完成。

如果沙箱支持可视化远程浏览器，应在上层产品提供“打开沙箱浏览器”的入口。`browser_interact` 只返回 `requires_user_action=true` 和结构化 `user_action`，不负责 UI。

## 验收用例

接入完成后至少用 `uv run` 覆盖以下验证：

```powershell
uv run python -m compileall src\chat\application\tools\browser src\chat\container.py src\chat\core\config\tool_settings.py
```

功能验收建议：

1. `navigate` 在本地 provider 正常创建会话。
2. `navigate` 在沙箱 provider 正常创建会话。
3. `snapshot` 返回 refs，且 iframe ref 可用于后续 `click_ref`。
4. `click_ref(expect_download=true)` 能在沙箱中观测下载触发。
5. `get_content` 在沙箱页面中继续返回 trafilatura 处理结果；trafilatura 空结果直接返回空。
6. 登录页或验证码页返回 `USER_INTERVENTION_REQUIRED`，不继续自动输入敏感信息。
7. `status` 能返回 runtime provider、engine、mode、network/dialog/console 摘要。
8. `close` 或工具生命周期结束时，沙箱资源被释放或租约归还。

## 推荐落地顺序

1. 在 `tool_settings.py` 增加沙箱 provider 配置。
2. 在 `BrowseInteractTool.__init__()` 和 `BrowserLaunchOptions` 透传沙箱配置。
3. 在 `RuntimeProvider` 增加 `SANDBOX_PLAYWRIGHT`。
4. 在 `runtime` 内新增 provider 边界，把本地启动逻辑从 `BrowserSessionManager` 中移出。
5. 实现 `LocalPlaywrightRuntimeProvider`，保持现有行为不变。
6. 实现 `SandboxPlaywrightRuntimeProvider`，只处理沙箱连接和资源释放。
7. 让 `BrowserSessionManager` 根据 `BrowserLaunchOptions.runtime_provider` 选择 provider。
8. 扩展下载事件 detail，沙箱 provider 有同步路径时返回 `sandbox_temp_path`。
9. 用本地 provider 和沙箱 provider 分别跑通核心 action。

## 不建议落地的做法

1. 不要在每个 action 内判断 `if sandbox`。
2. 不要把沙箱 endpoint 写死在 `session.py` 或 action 文件。
3. 不要让 `browser_interact` 直接处理下载文件内容。
4. 不要绕过现有 `snapshot/ref` 机制直接暴露 Playwright locator 给上层。
5. 不要在 models/enums/errors 中混入 provider 构建、response 构建或格式化逻辑。

