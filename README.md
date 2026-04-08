# WisePen Chat Service API 文档

**Base URL**: `http://<service-host>:<service-port>/chat`

## 1. 全局约定

### 1.1 全局 Headers
为了安全与网关通信，所有受保护的接口都需要在请求头中携带以下字段：

| Header Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `X-From-Source` | string | Yes | 防绕过 Token，需与配置的 `FROM_SOURCE_SECRET` 一致。 |
| `X-User-Id` | string | Yes | 当前登录用户的唯一 ID，由网关透传。 |

### 1.2 统一响应结构 (非流式接口)
非流式接口均返回统一的 `R` 泛型结构。针对分页接口，返回 `PageResult` 结构。

**普通返回 (`R<T>`)**:
```json
{
  "code": 200,          // 状态码，200 为成功
  "msg": "success",     // 状态描述信息
  "data": {}            // 具体的业务数据结构 T
}
```

**分页返回 (`R<PageResult<T>>`)**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "list": [],         // 当前页的数据列表 (List<T>)
    "total": 100,       // 总数据条数
    "page": 1,          // 当前页码
    "size": 20,         // 每页条数
    "total_page": 5     // 总页数
  }
}
```

### 1.3 全局业务错误码
在 `R<T>` 的响应中，如果 `code != 200`，表示业务异常。常见的 `chat` 服务专属错误码如下：

| Error Code | HTTP Status | Message | Description |
| :--- | :--- | :--- | :--- |
| `40001` | 400 | 目标会话不存在 | 请求了一个不存在的 `session_id`。 |
| `40002` | 400 | 对话上下文超出模型限制 | 用户输入或历史记录的 Token 数量超出了设定模型支持的最大上限。 |
| `50011` | 500 | 大模型生成失败 | 大语言模型接口调用报错（例如 OpenAI 或 Nacos 上的模型代理异常）。 |
| `40001` | 400 | 目标记忆不存在 | （针对 Memory 操作）请求了不存在的 `memory_id`。 |
| `50021` | 500 | 记忆操作失败 | 长期记忆相关的增删改查报错。 |

---

## 2. 聊天接口 (Chat)

### 2.1 发送对话消息
发起大语言模型对话，并以流式 Server-Sent Events (SSE) 输出结果。该流输出格式兼容 **Vercel AI SDK** 的事件格式。

- **URL**: `/completions`
- **Method**: `POST`
- **Content-Type**: `application/json`
- **Response**: `text/event-stream`

**Request Body (`ChatRequest`)**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `session_id` | string | Yes | 当前会话 ID。须在 `/session/createSession` 中获取。 |
| `query` | string | Yes | 用户发送的问题内容。 |
| `model` | string | No | 模型名称（如 `gpt-4o`），不传则使用系统默认模型。 |
| `states` | array | No | 上下文状态列表（如页面选中的文本），元素格式: `{"key": "...", "value": "...", "disabled": false}` |

**Response (Stream 事件说明)**:
接口将以 `text/plain; charset=utf-8` 格式返回纯文本流，该流严格遵循 **Vercel AI SDK Data Stream Protocol (v1)**，由单字符前缀和 JSON 字符串组成，以 `\n` 分隔。

常见的事件流结构如下：
*   `0:"..."`：(Text) 普通文本内容增量。
*   `g:"..."`：(Reasoning) 深度推理/思考内容的增量。
*   `9:{"toolCallId":"...","toolName":"...","args":{...}}`：(Tool Call) 完整的工具调用参数。
*   `a:{"toolCallId":"...","result":...}`：(Tool Result) 工具执行完成后的返回结果。
*   `2:[...]`：(Data) 自定义业务数据（如后端下发的调试日志、消耗积分等）。
*   `8:[...]`：(Message Annotations) 消息元数据注解。
*   `h:{"sourceId":"...","documentId":"...","url":"...","title":"..."}`：(Source) 引用来源，用于 RAG 检索展示。
*   `e:{"finishReason":"..."}`：(Finish Step) 推理步骤结束。
*   `d:{"finishReason":"..."}`：(Finish Message) 整条消息生成完毕。
*   `3:"..."`：(Error) 流中断或发生错误。

### 2.2 前端接入示例 (React + Vercel AI SDK)

在 React 项目中，强烈推荐使用 `@ai-sdk/react` 提供的 `useChat` 钩子，它会自动处理复杂的单字符流协议解析，并将其映射为结构化的 `messages` 数组。

**依赖安装**:
```bash
npm install @ai-sdk/react
```

**示例代码**:
```jsx
import { useChat } from '@ai-sdk/react'

export default function ChatComponent() {
  const { messages, input, handleInputChange, handleSubmit, status, error } = useChat({
    api: 'http://<service-host>:<service-port>/chat/completions',
    id: '当前会话ID',
    // 重写 fetch 以携带全局自定义 Headers
    fetch: async (url, options) => {
      const headers = new Headers(options?.headers);
      headers.set('X-User-Id', '10001');
      headers.set('X-From-Source', 'APISIX-wX0iR6tY');
      return fetch(url, { ...options, headers });
    },
    // (可选) 监听自定义的 2: 数据
    onData: (data) => {
      console.log('收到后端自定义数据:', data);
    }
  });

  return (
    <div>
      {/* 渲染消息列表 */}
      {messages.map(m => (
        <div key={m.id}>
          <strong>{m.role === 'user' ? 'User: ' : 'AI: '}</strong>
          
          {/* 渲染普通文本 */}
          {m.parts?.map((part, i) => {
            if (part.type === 'text') return <span key={i}>{part.text}</span>;
            if (part.type === 'reasoning') return <div key={i}>🧠 思考中: {part.text}</div>;
            return null;
          })}

          {/* 渲染工具调用状态 */}
          {m.toolInvocations?.map(tool => (
            <div key={tool.toolCallId}>
              🔧 正在调用 {tool.toolName}... 
              {tool.state === 'result' && <span>结果: {JSON.stringify(tool.result)}</span>}
            </div>
          ))}
        </div>
      ))}

      {error && <div style={{color: 'red'}}>{error.message}</div>}

      {/* 发送框 */}
      <form onSubmit={handleSubmit}>
        <input 
          value={input} 
          onChange={handleInputChange} 
          placeholder="向 AI 提问..."
          disabled={status === 'streaming'}
        />
        <button type="submit">发送</button>
      </form>
    </div>
  );
}
```

---

## 3. 会话管理接口 (Session)

### 3.1 创建新会话
**业务场景**: 当用户点击“新建对话”按钮时调用。会生成一个具有全局唯一 `id` 的空对话框。
- **URL**: `/session/createSession`
- **Method**: `POST`

**Request Body**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `title` | string | No | 会话标题。默认将填充为 "New Chat"。后续可通过重命名接口修改。 |

**Response `data` (`SessionResponse`)**:
```json
{
  "id": "64b5f9...",        // 会话唯一标识，用于后续发起对话
  "user_id": "u123",        // 归属用户
  "title": "New Chat",      // 标题
  "is_pinned": false,       // 默认非置顶
  "created_at": "2026-04-08T10:00:00Z",
  "updated_at": "2026-04-08T10:00:00Z"
}
```

### 3.2 获取会话列表 (分页)
**业务场景**: 渲染左侧边栏历史记录。通常情况下，数据会根据 `updated_at` 倒序排列，并且带有 `is_pinned=true` 的会话会被提取到“置顶”分类中单独展示。
- **URL**: `/session/listSessions`
- **Method**: `GET`

**Query Parameters**:
| Field | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `page` | int | No | `1` | 页码，从 1 开始 |
| `size` | int | No | `20` | 每页条数，建议前端支持无限滚动或分页按钮 |

**Response `data` (`PageResult<SessionResponse>`)**:
```json
{
  "list": [
    {
      "id": "64b5f9...",
      "user_id": "u123",
      "title": "React 性能优化",
      "is_pinned": true,
      "created_at": "2026-04-08T10:00:00Z",
      "updated_at": "2026-04-08T10:00:00Z"
    }
  ],
  "total": 45,
  "page": 1,
  "size": 20,
  "total_page": 3
}
```

### 3.3 删除会话
**业务场景**: 用户点击会话列表中的“垃圾桶”图标。该操作是硬删除（或软删除视后端配置），将同时级联删除该会话下的所有历史消息节点。
- **URL**: `/session/deleteSession`
- **Method**: `POST`

**Query Parameters**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `session_id` | string | Yes | 需要删除的目标会话 ID |

**Response `data`**: `null`。HTTP 200 且 `code=200` 表示删除成功。

### 3.4 获取会话历史消息 (分页)
**业务场景**: 当用户点击左侧历史会话，或者刷新页面时，通过此接口加载并恢复当前聊天框的历史消息。
**注**: 为保证前端渲染的纯净性，该接口通常过滤掉了系统内部的 `tool` 节点和隐式 Prompt，仅返回 `user` (用户提问) 和 `assistant` (AI 的最终回答)。
- **URL**: `/session/listHistoryMessages`
- **Method**: `GET`

**Query Parameters**:
| Field | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `session_id`| string| Yes | - | 目标会话 ID |
| `page` | int | No | `1` | 历史消息建议按时间正序排列（或倒序前端反转） |
| `size` | int | No | `20` | 单次加载量 |

**Response `data` (`PageResult<MessageResponse>`)**:
```json
{
  "list": [
    {
      "id": "msg_001",
      "role": "user",
      "content": "请用 Python 写一个快排",
      "tool_calls": null,
      "created_at": "2026-04-08T10:00:00Z"
    },
    {
      "id": "msg_002",
      "role": "assistant",
      "content": "好的，这里是快速排序的代码...",
      "tool_calls": null,
      "created_at": "2026-04-08T10:00:05Z"
    }
  ],
  "total": 2,
  "page": 1,
  "size": 20,
  "total_page": 1
}
```

### 3.5 重命名会话
**业务场景**: 可由用户手动触发（点击编辑图标），也可以在首轮对话完成后，由后端或前端通过提取关键字静默调用，实现会话自动命名。
- **URL**: `/session/renameSession`
- **Method**: `POST`

**Query Parameters**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `session_id`| string| Yes | 目标会话 ID |

**Request Body (`RenameSessionRequest`)**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `new_title` | string | No | 新的会话标题。如果不传或为空字符串，后端可能恢复默认名。 |

**Response `data` (`SessionResponse`)**: 返回更新后的会话实体。

### 3.6 会话置顶/取消置顶
**业务场景**: 用户将常用的工作流会话固定在左侧列表的顶部。
- **URL**: `/session/pinSession`
- **Method**: `POST`

**Query Parameters**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `session_id`| string| Yes | 目标会话 ID |

**Request Body (`PinSessionRequest`)**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `set_pin` | boolean | No | 传入 `true` 表示置顶，`false` 表示取消置顶。默认为 `false`。 |

**Response `data` (`SessionResponse`)**: 返回更新后的会话实体，其 `is_pinned` 属性将发生改变。

---

## 4. 记忆管理接口 (Memory)

### 4.1 获取所有长期记忆
**业务场景**: AI 在后台执行对话时，Mem0 系统会自动抽取关于该用户的长期事实。本接口用于前端构建“记忆控制台”，让用户透视 AI 究竟记住了什么。
- **URL**: `/memory/listMemories`
- **Method**: `GET`

**Response `data` (`List<MemoryItemResponse>`)**:
```json
[
  {
    "id": "mem_001",
    "memory": "用户偏好使用 Python 和 React 进行全栈开发",
    "metadata": {
      "created_at": "2026-04-01T12:00:00Z"
    }
  }
]
```

### 4.2 删除单条记忆
**业务场景**: 当 Mem0 发生提取错误（幻觉），或者用户的偏好发生改变时，用户可以在“记忆控制台”中手动删除错误的记忆节点，防止后续对话受此影响。
- **URL**: `/memory/deleteMemory`
- **Method**: `POST`

**Query Parameters**:
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `memory_id`| string| Yes | 需要清除的记忆条目 UUID / ID |

**Response `data`**: `null`，HTTP 200 且 `code=200` 表示删除成功。

### 4.3 清空全部记忆
**业务场景**: 账号注销、隐私合规（GDPR 遗忘权）要求，或者用户希望 AI 彻底“失忆”重新开始学习。
- **URL**: `/memory/deleteAllMemories`
- **Method**: `DELETE`

**Response `data`**: `null`，HTTP 200 且 `code=200` 表示清空成功。

---

## 5. 模型配置接口 (Model)

### 5.1 获取模型列表
**业务场景**: 渲染前端顶部（或侧边栏）的下拉模型选择器。接口已按类型（`type`）完成聚合分组，前端可直接映射为 `<optgroup>` 标签。
- **URL**: `/model/listModels`
- **Method**: `GET`

**关于字段说明**:
*   `ratio`: 代表该模型的计费费率比例（例如 `gpt-4o` 可能是 `gpt-4o-mini` 消耗的 10 倍）。前端可在 UI 上透出类似 `10x 消耗` 的提示。
*   `is_default`: 默认推荐模型，前端在未选中任何模型时应 fallback 到该项。

**Response `data` (`ModelsResponse`)**:
```json
{
  "standard_models": [
    {
      "id": "gpt-4o-mini",
      "name": "GPT-4o Mini",
      "type": 1,
      "providers": [{"provider_id": 1, "model_id": "gpt-4o-mini"}],
      "ratio": 1,
      "is_default": false
    }
  ],
  "advanced_models": [
    {
      "id": "gpt-4o",
      "name": "GPT-4o",
      "type": 2,
      "providers": [{"provider_id": 1, "model_id": "gpt-4o"}],
      "ratio": 10,
      "is_default": true
    }
  ],
  "other_models": []
}
```