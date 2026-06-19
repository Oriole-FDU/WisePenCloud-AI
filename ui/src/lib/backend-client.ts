import type {
  AvailableModelsResponse,
  BackendEnvelope,
  ChatAppMessage,
  ModelInfo,
  ModelOption,
  PageResult,
  RuntimeSettings,
  WebSearchCredential,
} from "../types/chat";

type SessionResponse = {
  id: string;
  title: string;
  is_pinned: boolean;
  created_at: string;
  updated_at: string;
};

type RequestOptions = {
  method?: string;
  body?: unknown;
};

function buildHeaders(settings: RuntimeSettings) {
  return {
    "Content-Type": "application/json",
    "X-From-Source": settings.fromSource,
    "X-User-Id": settings.userId,
    "X-Identity-Type": settings.identityType,
  };
}

async function request<T>(
  settings: RuntimeSettings,
  path: string,
  options: RequestOptions = {},
) {
  const response = await fetch(`${settings.baseUrl}${path}`, {
    body: options.body ? JSON.stringify(options.body) : undefined,
    headers: buildHeaders(settings),
    method: options.method ?? "GET",
  });

  const payload = await response.json() as BackendEnvelope<T>;

  if (!response.ok || payload.code !== 200) {
    throw new Error(payload.msg || `Request failed: ${response.status}`);
  }

  return payload.data;
}

export async function createSession(settings: RuntimeSettings) {
  return request<SessionResponse>(settings, "/session/createSession", {
    body: { title: "新的对话" },
    method: "POST",
  });
}

export async function listSessions(settings: RuntimeSettings, page = 1, size = 50) {
  return request<PageResult<SessionResponse>>(settings, `/session/listSessions?page=${page}&size=${size}`);
}

export async function deleteSession(settings: RuntimeSettings, sessionId: string) {
  return request<null>(settings, `/session/deleteSession?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
  });
}

export async function renameSession(settings: RuntimeSettings, sessionId: string, newTitle: string) {
  return request<SessionResponse>(settings, `/session/renameSession?session_id=${encodeURIComponent(sessionId)}`, {
    body: { new_title: newTitle },
    method: "POST",
  });
}

export async function pinSession(settings: RuntimeSettings, sessionId: string, setPin: boolean) {
  return request<SessionResponse>(settings, `/session/pinSession?session_id=${encodeURIComponent(sessionId)}`, {
    body: { set_pin: setPin },
    method: "POST",
  });
}

export async function loadHistoryMessages(settings: RuntimeSettings, sessionId: string) {
  return request<PageResult<ChatAppMessage>>(settings, `/session/listHistoryMessages?session_id=${encodeURIComponent(sessionId)}&page=1&size=50`);
}

export async function listAvailableModels(settings: RuntimeSettings) {
  const response = await request<AvailableModelsResponse>(settings, "/model/listAvailableModels");
  return [
    ...modelOptions(response.system_models, "平台模型"),
    ...modelOptions(response.user_models, "自定义模型"),
  ];
}

export async function listWebSearchCredentials(settings: RuntimeSettings) {
  return request<WebSearchCredential[]>(settings, "/webSearch/listWebSearchCredentials");
}

export async function setPlatformMembership(settings: RuntimeSettings, isMember: boolean) {
  return request<WebSearchCredential>(settings, "/webSearch/setPlatformMembership", {
    body: { is_member: isMember },
    method: "POST",
  });
}

export async function createWebSearchCredential(settings: RuntimeSettings, provider: string, apiKey: string) {
  return request<WebSearchCredential>(settings, "/webSearch/createWebSearchCredential", {
    body: {
      provider,
      source: "custom",
      api_key: apiKey,
    },
    method: "POST",
  });
}

export async function setActiveWebSearchCredential(
  settings: RuntimeSettings,
  source: "platform" | "custom",
  provider: string,
) {
  return request<WebSearchCredential>(settings, "/webSearch/setActiveWebSearchCredential", {
    body: {
      source,
      provider,
    },
    method: "POST",
  });
}

function modelOptions(models: ModelInfo[], group: string): ModelOption[] {
  return models.flatMap((model) => {
    const activeMappings = (model.mappings ?? [])
      .filter((mapping) => mapping.is_active)
      .sort((left, right) => {
        if (left.is_preferred !== right.is_preferred) {
          return left.is_preferred ? -1 : 1;
        }

        return left.priority - right.priority;
      });

    if (activeMappings.length === 0) {
      return [{
        value: `${model.id}:`,
        modelId: model.id,
        providerId: "",
        label: model.display_name,
        description: `${group} · ${model.vendor}`,
        billingRatio: model.billing_ratio,
        supportTools: model.support_tools,
        supportVision: model.support_vision,
        supportThinking: model.support_thinking,
      }];
    }

    return activeMappings.map((mapping) => ({
      value: `${model.id}:${mapping.provider_id}`,
      modelId: model.id,
      providerId: mapping.provider_id,
      label: model.display_name,
      description: `${group} · ${mapping.provider_name ?? mapping.provider_model_name}`,
      billingRatio: model.billing_ratio,
      supportTools: model.support_tools,
      supportVision: model.support_vision,
      supportThinking: model.support_thinking,
    }));
  });
}
