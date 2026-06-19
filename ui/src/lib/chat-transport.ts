import { DefaultChatTransport, isTextUIPart, type UIMessage } from "ai";
import type { RuntimeSettings } from "../types/chat";

type BackendTransportOptions = {
  getSessionId: () => string | undefined;
  settings: RuntimeSettings;
};

export function createBackendTransport({ getSessionId, settings }: BackendTransportOptions) {
  console.log("createBackendTransport called", { baseUrl: settings.baseUrl, modelId: settings.modelId, providerId: settings.providerId });

  return new DefaultChatTransport<UIMessage>({
    api: `${settings.baseUrl}/completions`,
    headers: {
      "X-From-Source": settings.fromSource,
      "X-User-Id": settings.userId,
      "X-Identity-Type": settings.identityType,
    },
    fetch: async (url, init) => {
      const sessionId = getSessionId();
      console.log("fetch called", { url, init, sessionId });

      if (!sessionId) {
        console.error("Session not initialized, sessionId is:", sessionId);
        throw new Error("Session not initialized");
      }

      // 解析原始请求体中的 messages
      let messages: UIMessage[] = [];
      if (init?.body) {
        console.log("Parsing body, type:", typeof init.body);
        try {
          const bodyStr = typeof init.body === "string" ? init.body : JSON.stringify(init.body);
          console.log("Body string:", bodyStr);
          const originalBody = JSON.parse(bodyStr);
          console.log("Parsed body:", originalBody);
          messages = originalBody.messages || [];
        } catch (e) {
          console.error("Failed to parse body:", e);
        }
      }

      console.log("Messages:", messages);

      // 获取最新的用户消息
      const latestUserMessage = [...messages].reverse().find((message: UIMessage) => message.role === "user");
      console.log("Latest user message:", latestUserMessage);
      const latestText = latestUserMessage?.parts?.find(isTextUIPart);
      console.log("Latest text:", latestText);

      // 构建自定义请求体
      const customBody = {
        session_id: sessionId,
        query: latestText?.text ?? "",
        model: settings.modelId || undefined,
        provider_id: settings.providerId || undefined,
      };

      console.log("Custom request body:", customBody);

      // 发送请求
      console.log("Sending request to:", url);
      const response = await fetch(url, {
        ...init,
        body: JSON.stringify(customBody),
      });

      console.log("Response:", response.status, response.statusText);

      return response;
    },
  });
}
