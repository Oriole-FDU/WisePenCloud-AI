import type { DynamicToolUIPart, ToolUIPart, UIMessage } from "ai";

export type ToolPart = ToolUIPart | DynamicToolUIPart;

export type SessionSummary = {
  id: string;
  title: string;
  updatedAt: string;
  preview: string;
  pinned?: boolean;
};

export type RuntimeSettings = {
  mode: "mock" | "backend";
  baseUrl: string;
  fromSource: string;
  userId: string;
  identityType: string;
  modelId: string;
  providerId: string;
  searchProvider: string;
  searchSource: "platform" | "custom";
};

export type ModelProviderMapping = {
  model_id: string;
  provider_id: string;
  provider_name: string | null;
  provider_model_name: string;
  is_preferred: boolean;
  is_active: boolean;
  priority: number;
};

export type ModelInfo = {
  id: string;
  scope: string;
  display_name: string;
  vendor: string;
  type: string;
  billing_ratio: number;
  support_thinking: boolean;
  support_vision: boolean;
  support_tools: boolean;
  support_streaming: boolean;
  context_window_tokens: number | null;
  max_output_tokens: number | null;
  is_active: boolean;
  mappings: ModelProviderMapping[] | null;
};

export type AvailableModelsResponse = {
  system_models: ModelInfo[];
  user_models: ModelInfo[];
};

export type ModelOption = {
  value: string;
  modelId: string;
  providerId: string;
  label: string;
  description: string;
  billingRatio: number;
  supportTools: boolean;
  supportVision: boolean;
  supportThinking: boolean;
};

export type WebSearchCredential = {
  user_id: string;
  provider: string;
  source: "platform" | "custom";
  is_member: boolean;
  api_key_masked: string;
  api_key_fingerprint: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type BackendEnvelope<T> = {
  code: number;
  msg: string;
  data: T;
};

export type PageResult<T> = {
  list: T[];
  total: number;
  page: number;
  size: number;
  total_page: number;
};

export type ChatAppMessage = UIMessage & {
  createdAt?: string;
};
