# WisePen Chat Service API 文档

## 概述

WisePen Chat Service 是一个智能对话服务，提供会话管理、模型调用、长期记忆等功能。

**基础路径**: `/v1`

**认证方式**: 
- `X-User-Id`: 用户ID（必填，纯数字字符串格式，如 `1712536789123456789`）
- `X-From-Source`: 来源标识（必填，需与服务端配置的密钥匹配）

**通用响应格式**:
```json
{
  "code": 200,
  "msg": "success",
  "data": { ... }
}
```

---

## 1. 会话管理 (Sessions)

### 1.1 创建会话

**POST** `/v1/sessions/create`

创建一个新的对话会话。

**请求体**:
```json
{
  "title": "新会话标题"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| title | string | 否 | 会话标题，默认 "New Chat" |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": "6804a1b2aac3a6b9747b43c1",
    "user_id": "1712536789123456789",
    "title": "新会话标题",
    "created_at": "2026-04-08T00:00:00.000Z",
    "updated_at": "2026-04-08T00:00:00.000Z",
    "is_pinned": false,
    "pinned_at": null
  }
}
```

---

### 1.2 获取会话列表

**GET** `/v1/sessions/list`

获取当前用户的会话列表，支持分页，置顶会话优先显示。

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 否 | 1 | 页码，从 1 开始 |
| size | int | 否 | 20 | 每页条数，最大 100 |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "list": [
      {
        "id": "6804a1b2aac3a6b9747b43c1",
        "user_id": "1712536789123456789",
        "title": "置顶的会话",
        "created_at": "2026-04-08T00:00:00.000Z",
        "updated_at": "2026-04-08T00:00:00.000Z",
        "is_pinned": true,
        "pinned_at": "2026-04-08T01:00:00.000Z"
      }
    ],
    "total": 10,
    "page": 1,
    "size": 20
  }
}
```

---

### 1.3 删除会话

**DELETE** `/v1/sessions/{session_id}`

删除指定会话及其所有消息。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | 会话ID |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": null
}
```

---

### 1.4 获取会话消息历史

**GET** `/v1/sessions/{session_id}/messages`

获取指定会话的消息历史记录。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | 会话ID |

**查询参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| page | int | 否 | 1 | 页码 |
| size | int | 否 | 20 | 每页条数 |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "list": [
      {
        "id": "6804a1b2aac3a6b9747b43c2",
        "role": "user",
        "content": "你好",
        "tool_calls": null,
        "created_at": "2026-04-08T00:00:00.000Z"
      },
      {
        "id": "6804a1b2aac3a6b9747b43c3",
        "role": "assistant",
        "content": "你好！有什么可以帮助你的吗？",
        "tool_calls": null,
        "created_at": "2026-04-08T00:00:01.000Z"
      }
    ],
    "total": 2,
    "page": 1,
    "size": 20
  }
}
```

---

### 1.5 重命名会话

**POST** `/v1/sessions/{session_id}/rename`

修改会话标题。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | 会话ID |

**请求体**:
```json
{
  "new_title": "新的会话标题"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| new_title | string | 否 | 新标题，默认 "New Chat" |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": "6804a1b2aac3a6b9747b43c1",
    "user_id": "1712536789123456789",
    "title": "新的会话标题",
    "created_at": "2026-04-08T00:00:00.000Z",
    "updated_at": "2026-04-08T01:00:00.000Z",
    "is_pinned": false,
    "pinned_at": null
  }
}
```

---

### 1.6 置顶/取消置顶会话

**POST** `/v1/sessions/{session_id}/pin`

设置或取消会话的置顶状态。置顶的会话会在列表中优先显示。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| session_id | string | 会话ID |

**请求体**:
```json
{
  "set_pin": true
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| set_pin | boolean | 是 | true=置顶，false=取消置顶 |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "id": "6804a1b2aac3a6b9747b43c1",
    "user_id": "1712536789123456789",
    "title": "会话标题",
    "created_at": "2026-04-08T00:00:00.000Z",
    "updated_at": "2026-04-08T01:00:00.000Z",
    "is_pinned": true,
    "pinned_at": "2026-04-08T01:00:00.000Z"
  }
}
```

---

## 2. 聊天 (Chat)

### 2.1 发送消息 (流式响应)

**POST** `/v1/chat/completions`

发送用户消息并获取 AI 流式响应。

**请求体**:
```json
{
  "session_id": "6804a1b2aac3a6b9747b43c1",
  "query": "请解释一下量子计算的基本原理",
  "model": "gpt-4o",
  "selected_text": "可选的选中文本"
}
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| session_id | string | 是 | 会话ID |
| query | string | 是 | 用户输入内容 |
| model | string | 否 | 指定模型，不传则使用服务端默认配置 |
| selected_text | string | 否 | 用户选择的原文（用于上下文引用） |

**响应**: Server-Sent Events (SSE) 流

```
data: {"type":"message-start","id":"msg_xxx","created":1234567890}

data: {"type":"text-start","id":"text-1"}

data: {"type":"text-delta","id":"text-1","delta":"量子计算"}

data: {"type":"text-delta","id":"text-1","delta":"是"}

data: {"type":"text-end","id":"text-1"}

data: {"type":"message-finish","id":"msg_xxx"}

data: [DONE]
```

**SSE 事件类型**:

| 类型 | 说明 |
|------|------|
| message-start | 消息开始，包含消息ID |
| text-start | 文本块开始 |
| text-delta | 文本增量内容 |
| text-end | 文本块结束 |
| reasoning-start | 推理过程开始（仅推理模型） |
| reasoning-delta | 推理过程增量内容 |
| reasoning-end | 推理过程结束 |
| tool-input-start | 工具调用开始 |
| tool-input-delta | 工具调用参数增量 |
| tool-input-end | 工具调用参数结束 |
| tool-output-available | 工具调用结果可用 |
| message-finish | 消息完成 |
| error | 错误信息 |
| abort | 请求中止 |

---

## 3. 模型管理 (Models)

### 3.1 获取可用模型列表

**GET** `/v1/models/list`

获取系统中所有可用的 AI 模型配置，按类型分类返回。

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "standard_models": [
      {
        "id": "gpt-4o-mini",
        "name": "GPT-4o Mini",
        "type": 1,
        "providers": [{"provider_id": 1, "model_id": "gpt-4o-mini"}],
        "ratio": 1,
        "is_default": true
      }
    ],
    "advanced_models": [
      {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "type": 2,
        "providers": [{"provider_id": 1, "model_id": "gpt-4o"}],
        "ratio": 10,
        "is_default": false
      },
      {
        "id": "gpt-5.4",
        "name": "GPT-5.4",
        "type": 2,
        "providers": [{"provider_id": 1, "model_id": "gpt-5.4"}],
        "ratio": 10,
        "is_default": false
      },
      {
        "id": "gemini-2.5-pro",
        "name": "Gemini 2.5 Pro",
        "type": 2,
        "providers": [{"provider_id": 1, "model_id": "gemini-2.5-pro-preview"}],
        "ratio": 10,
        "is_default": false
      },
      {
        "id": "deepseek-reasoner",
        "name": "DeepSeek Reasoner",
        "type": 2,
        "providers": [{"provider_id": 1, "model_id": "deepseek-reasoner"}],
        "ratio": 10,
        "is_default": false
      },
      {
        "id": "qwen-max",
        "name": "Qwen Max",
        "type": 2,
        "providers": [{"provider_id": 1, "model_id": "qwen-max"}],
        "ratio": 10,
        "is_default": false
      }
    ],
    "other_models": []
  }
}
```

**模型类型 (type)**:

| 值 | 说明 | 费率 |
|------|------|------|
| 1 | 标准模型 | x1 |
| 2 | 高级模型 | x10 |
| 3 | 未知模型 | x1 |

**供应商 (provider_id)**:

| 值 | 说明 |
|------|------|
| 1 | 智增增 (ZHIZENGZENG) |
| 2 | API易 (APIYI) |
| 3 | ModelScope |

**当前支持的模型**:

| 类型 | 模型ID | 显示名称 | 费率 |
|------|--------|----------|------|
| 标准 | gpt-4o-mini | GPT-4o Mini | x1 |
| 高级 | gpt-4o | GPT-4o | x10 |
| 高级 | gpt-5.4 | GPT-5.4 | x10 |
| 高级 | gemini-2.5-pro | Gemini 2.5 Pro | x10 |
| 高级 | deepseek-reasoner | DeepSeek Reasoner | x10 |
| 高级 | qwen-max | Qwen Max | x10 |

---

## 4. 记忆管理 (Memories)

### 4.1 获取用户记忆列表

**GET** `/v1/memories/list`

获取当前用户的所有长期记忆条目。

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": [
    {
      "id": "mem_xxx",
      "memory": "用户喜欢使用 Python 进行数据分析",
      "metadata": {
        "created_at": "2026-04-08T00:00:00.000Z",
        "source": "chat"
      }
    }
  ]
}
```

---

### 4.2 删除单条记忆

**DELETE** `/v1/memories/{memory_id}`

删除指定的记忆条目。

**路径参数**:

| 参数 | 类型 | 说明 |
|------|------|------|
| memory_id | string | 记忆ID |

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": null
}
```

---

### 4.3 清空所有记忆

**DELETE** `/v1/memories`

清空当前用户的所有长期记忆（用于隐私合规注销等场景）。

**响应**:
```json
{
  "code": 200,
  "msg": "success",
  "data": null
}
```

---

## 5. 错误码

| 错误码 | 说明 |
|--------|------|
| 200 | 成功 |
| 400 | 请求参数错误 |
| 401 | 未授权（缺少或无效的认证信息） |
| 404 | 资源不存在 |
| 500 | 服务器内部错误 |

**业务错误码**:

| 错误码 | 说明 |
|--------|------|
| SESSION_NOT_FOUND | 会话不存在 |
| CONTEXT_LIMIT_EXCEEDED | 上下文长度超限 |
| LLM_GENERATION_FAILED | LLM 生成失败 |
| MEMORY_OPERATION_FAILED | 记忆操作失败 |

---

## 6. 前端对接示例

### 6.1 创建会话并发送消息

```javascript
// 生成用户ID（纯数字字符串）
let userId = localStorage.getItem('wisepen_user_id')
if (!userId) {
  userId = String(Date.now()) + String(Math.floor(Math.random() * 1000000))
  localStorage.setItem('wisepen_user_id', userId)
}

// 1. 创建会话
const createRes = await fetch('/v1/sessions/create', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-User-Id': userId,
    'X-From-Source': 'APISIX-xxx'
  },
  body: JSON.stringify({ title: '新对话' })
});
const { data: session } = await createRes.json();
const sessionId = session.id;

// 2. 发送消息（流式）
const response = await fetch('/v1/chat/completions', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-User-Id': userId,
    'X-From-Source': 'APISIX-xxx',
    'Accept': 'text/event-stream'
  },
  body: JSON.stringify({
    session_id: sessionId,
    query: '你好',
    model: 'gpt-4o'
  })
});

const reader = response.body.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  
  const text = decoder.decode(value);
  // 解析 SSE 事件...
}
```

### 6.2 获取模型列表

```javascript
const response = await fetch('/v1/models/list', {
  headers: {
    'X-User-Id': userId,
    'X-From-Source': 'APISIX-xxx'
  }
});
const json = await response.json();
const data = json.data;

// 合并所有模型
const allModels = [
  ...(data.standard_models || []),
  ...(data.advanced_models || []),
  ...(data.other_models || []),
];

// 查找默认模型
const defaultModel = allModels.find(m => m.is_default)?.id || allModels[0]?.id;
```

### 6.3 置顶会话

```javascript
await fetch(`/v1/sessions/${sessionId}/pin`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-User-Id': userId,
    'X-From-Source': 'APISIX-xxx'
  },
  body: JSON.stringify({ set_pin: true })
});
```

### 6.4 重命名会话

```javascript
await fetch(`/v1/sessions/${sessionId}/rename`, {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'X-User-Id': userId,
    'X-From-Source': 'APISIX-xxx'
  },
  body: JSON.stringify({ new_title: '新标题' })
});
```

---

## 7. 数据模型

### SessionResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 会话ID |
| user_id | string | 用户ID（纯数字字符串） |
| title | string | 会话标题 |
| created_at | string | 创建时间 (ISO 8601) |
| updated_at | string | 更新时间 (ISO 8601) |
| is_pinned | boolean | 是否置顶 |
| pinned_at | string\|null | 置顶时间 |

### MessageResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 消息ID |
| role | string | 角色 (user/assistant/tool) |
| content | string\|null | 消息内容 |
| tool_calls | array\|null | 工具调用列表 |
| created_at | string | 创建时间 |

### ModelInfo

| 字段 | 类型 | 说明 |
|------|------|------|
| id | string | 模型ID |
| name | string | 模型显示名称 |
| type | int | 模型类型 (1=标准, 2=高级, 3=未知) |
| providers | array | 供应商映射列表 |
| ratio | int | 费率倍数 (1 或 10) |
| is_default | boolean | 是否为默认模型 |

### ModelsResponse

| 字段 | 类型 | 说明 |
|------|------|------|
| standard_models | array[ModelInfo] | 标准模型列表 (ratio=1) |
| advanced_models | array[ModelInfo] | 高级模型列表 (ratio=10) |
| other_models | array[ModelInfo] | 其他模型列表 |
