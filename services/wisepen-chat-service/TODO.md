# Chat Service 待办事项

## 平台级多模态模型输入能力

- [ ] 建立端到端的多模态输入契约，完成前不得将图片视为模型可直接读取的上下文。

当前状态：`Model.support_vision` 目前只记录模型是否声明支持视觉输入。聊天请求、
`ChatMessage`、上下文组装、Token 计数、历史持久化、工具结果注入和 Provider
formatter 当前都以文本形式传递用户内容和工具内容。因此，附件 metadata、Markdown
图片语法、图片 URL 以及内嵌 data URI 都不会让图片像素真正进入模型输入。

完成该平台能力至少需要覆盖以下事项：

- 定义 Provider 无关的消息内容部件模型，支持文本和图片，并包含 MIME 类型以及可解析的 URL、文件引用或编码后的载荷。
- 通过受授权的文件边界解析附件和 Markdown 图片引用，不能暴露私有 object key，也不能无约束地放行远程 URL。
- 确保多模态内容能够贯穿 Prompt 组装、短期历史、持久化、摘要和工具结果续接流程。
- 在 OpenAI、Anthropic、Gemini、Qwen 和 LiteLLM adapter 中，将统一的图片部件转换为各 Provider 所需的请求格式；对于不支持的模型必须有明确的降级或拒绝行为。
- 将图片输入纳入上下文预算和 Provider 用量处理。
- 增加端到端测试，证明图片最终以图片部件进入 Provider 请求，而不是退化为 Markdown 文本或附件 metadata。

在该待办完成之前，图片解析和 chunk metadata 最多只能支持图片发现与定位，不能宣称平台已经支持图片理解。
