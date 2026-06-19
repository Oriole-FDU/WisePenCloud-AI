import { useEffect, useEffectEvent, useMemo, useRef, useState } from "react";
import { useChat } from "@ai-sdk/react";
import { randomId } from "./lib/utils";
import { createBackendTransport } from "./lib/chat-transport";
import {
  createSession,
  createWebSearchCredential,
  deleteSession,
  listAvailableModels,
  listSessions,
  listWebSearchCredentials,
  loadHistoryMessages,
  pinSession,
  renameSession,
  setActiveWebSearchCredential,
  setPlatformMembership,
} from "./lib/backend-client";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { ChatWindow } from "./components/ChatWindow";
import { InputBox } from "./components/InputBox";
import { SettingsDialog } from "./components/SettingsDialog";
import { Sidebar } from "./components/Sidebar";
import {
  defaultRuntimeSettings,
  mockMessages,
  mockModelOptions,
  mockSearchCredentials,
} from "./data/mockConversation";
import type { ChatAppMessage, ModelOption, RuntimeSettings, SessionSummary, WebSearchCredential } from "./types/chat";

function convertMessages(messages: typeof mockMessages): ChatAppMessage[] {
  return messages.map((message) => ({
    ...message,
    createdAt: message.createdAt ?? new Date().toISOString(),
  }));
}

function maskApiKey(value: string) {
  const trimmed = value.trim();

  if (trimmed.length <= 8) {
    return "*".repeat(trimmed.length);
  }

  return `${trimmed.slice(0, 4)}***${trimmed.slice(-4)}`;
}

export default function App() {
  const [settings, setSettings] = useState<RuntimeSettings>(defaultRuntimeSettings);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const [input, setInput] = useState("");
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string>("");
  const [backendSessionId, setBackendSessionId] = useState<string>("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelLoadError, setModelLoadError] = useState("");
  const [searchCredentials, setSearchCredentials] = useState<WebSearchCredential[]>([]);
  const [searchLoadError, setSearchLoadError] = useState("");
  const [mockSearchCredentialState, setMockSearchCredentialState] = useState<WebSearchCredential[]>(mockSearchCredentials);
  const [mockChatMessages, setMockChatMessages] = useState<ChatAppMessage[]>(() => convertMessages(mockMessages));
  const backendSessionIdRef = useRef(backendSessionId);
  backendSessionIdRef.current = backendSessionId;

  const transport = useMemo(() => {
    return createBackendTransport({
      getSessionId: () => backendSessionIdRef.current,
      settings,
    });
  }, [settings.baseUrl, settings.fromSource, settings.identityType, settings.modelId, settings.providerId]);
  const { messages, sendMessage, setMessages, status, stop } = useChat({
    transport,
  });
  const applyHistoryMessages = useEffectEvent((nextMessages: ChatAppMessage[]) => {
    setMessages(nextMessages);
  });
  const applyModelOptions = useEffectEvent((options: ModelOption[]) => {
    setModelOptions(options);

    if (options[0]) {
      setSettings((current) => {
        if (current.modelId) {
          return current;
        }

        return {
          ...current,
          modelId: options[0].modelId,
          providerId: options[0].providerId,
        };
      });
    }
  });
  const bootstrapSession = useEffectEvent(async () => {
    console.log("bootstrapSession called, settings:", settings);
    try {
      // 加载已有 session 列表
      const sessionsResult = await listSessions(settings);
      const sessionList: SessionSummary[] = sessionsResult.list.map((s) => ({
        id: s.id,
        title: s.title,
        updatedAt: s.updated_at,
        preview: "",
        pinned: s.is_pinned,
      }));
      setSessions(sessionList);

      // 如果有已有 session，选择最近的一个；否则创建新 session
      if (sessionList.length > 0) {
        const latestSession = sessionList[0];
        setBackendSessionId(latestSession.id);
        setActiveSessionId(latestSession.id);
        const history = await loadHistoryMessages(settings, latestSession.id);
        applyHistoryMessages(history.list);
      } else {
        const session = await createSession(settings);
        setBackendSessionId(session.id);
        setActiveSessionId(session.id);
        setSessions([{
          id: session.id,
          title: session.title,
          updatedAt: session.updated_at,
          preview: "",
          pinned: session.is_pinned,
        }]);
      }
    } catch (error) {
      console.error("bootstrapSession error:", error);
    }
  });

  function applySearchCredentials(credentials: WebSearchCredential[]) {
    setSearchCredentials(credentials);

    // 优先找 active 的 custom，其次找 active 的 platform
    const activeCustom = credentials.find((credential) => credential.source === "custom" && credential.is_active);
    const activePlatform = credentials.find((credential) => credential.source === "platform" && credential.is_active);
    const selected = activeCustom ?? activePlatform;

    if (selected) {
      setSettings((current) => {
        if (
          current.searchProvider === selected.provider
          && current.searchSource === selected.source
        ) {
          return current;
        }

        return {
          ...current,
          searchProvider: selected.provider,
          searchSource: selected.source,
        };
      });
    }
  }

  useEffect(() => {
    if (settings.mode !== "backend") {
      return;
    }

    let disposed = false;
    const requestSettings: RuntimeSettings = {
      ...defaultRuntimeSettings,
      baseUrl: settings.baseUrl,
      fromSource: settings.fromSource,
      identityType: settings.identityType,
      userId: settings.userId,
    };

    async function loadRuntimeOptions() {
      setModelLoadError("");
      setSearchLoadError("");

      const [nextModels, nextCredentials] = await Promise.all([
        listAvailableModels(requestSettings),
        listWebSearchCredentials(requestSettings),
      ]);

      if (disposed) {
        return;
      }

      applyModelOptions(nextModels);
      applySearchCredentials(nextCredentials);
    }

    void loadRuntimeOptions().catch((error: unknown) => {
      if (disposed) {
        return;
      }

      const message = error instanceof Error ? error.message : "运行配置加载失败";
      setModelLoadError(message);
      setSearchLoadError(message);
    });

    return () => {
      disposed = true;
    };
  }, [
    settings.baseUrl,
    settings.fromSource,
    settings.identityType,
    settings.mode,
    settings.userId,
  ]);

  useEffect(() => {
    if (settings.mode !== "backend") {
      return;
    }

    let disposed = false;

    async function run() {
      try {
        await bootstrapSession();
      } catch (error) {
        if (disposed) return;
        console.error("Failed to bootstrap session:", error);
      }
    }

    void run();

    return () => {
      disposed = true;
    };
  }, [
    settings.baseUrl,
    settings.fromSource,
    settings.identityType,
    settings.mode,
    settings.userId,
  ]);

  // 切换 session 时加载历史消息
  useEffect(() => {
    if (settings.mode !== "backend" || !activeSessionId) {
      return;
    }

    // 如果是当前已加载的 session，不需要重新加载
    if (activeSessionId === backendSessionId) {
      return;
    }

    let disposed = false;

    async function loadSessionHistory() {
      try {
        setBackendSessionId(activeSessionId);
        const history = await loadHistoryMessages(settings, activeSessionId);
        if (disposed) return;
        applyHistoryMessages(history.list);
      } catch (error) {
        if (disposed) return;
        console.error("Failed to load session history:", error);
      }
    }

    void loadSessionHistory();

    return () => {
      disposed = true;
    };
  }, [activeSessionId, settings.mode]);

  const displayedMessages = settings.mode === "mock"
    ? mockChatMessages
    : (messages as ChatAppMessage[]);
  const displayedModelOptions = settings.mode === "mock" ? mockModelOptions : modelOptions;
  const displayedSearchCredentials = settings.mode === "mock" ? mockSearchCredentialState : searchCredentials;
  const displayedModelLoadError = settings.mode === "mock" ? "" : modelLoadError;
  const displayedSearchLoadError = settings.mode === "mock" ? "" : searchLoadError;

  function submitMockMessage() {
    const userMessage: ChatAppMessage = {
      id: randomId("msg_user"),
      role: "user",
      metadata: undefined,
      createdAt: new Date().toISOString(),
      parts: [{ type: "text", text: input }],
    };

    const assistantMessage: ChatAppMessage = {
      ...mockMessages[1],
      id: randomId("msg_assistant"),
      createdAt: new Date().toISOString(),
    };

    setMockChatMessages((current) => [...current, userMessage, assistantMessage]);
    setInput("");
  }

  function handleSubmit() {
    console.log("handleSubmit called", { input, mode: settings.mode, backendSessionId });

    if (!input.trim()) {
      return;
    }

    if (settings.mode === "mock") {
      submitMockMessage();
      return;
    }

    if (!backendSessionId) {
      console.error("No backendSessionId, cannot send message");
      return;
    }

    console.log("Sending message...");
    void sendMessage({ text: input });
    setInput("");
  }

  async function handleCreateSession() {
    if (settings.mode === "backend") {
      try {
        const session = await createSession(settings);
        const nextSession: SessionSummary = {
          id: session.id,
          title: session.title,
          updatedAt: session.updated_at,
          preview: "",
          pinned: session.is_pinned,
        };

        setSessions((current) => [nextSession, ...current]);
        setActiveSessionId(session.id);
        setBackendSessionId(session.id);
        setMessages([]);
      } catch (error) {
        console.error("Failed to create session:", error);
      }
    } else {
      const sessionId = randomId("session");
      const nextSession: SessionSummary = {
        id: sessionId,
        title: "新的对话",
        updatedAt: new Date().toISOString(),
        preview: "等待第一条消息",
      };

      setSessions((current) => [nextSession, ...current]);
      setActiveSessionId(sessionId);
    }
  }

  async function handleDeleteSession(sessionId: string) {
    if (settings.mode === "backend") {
      try {
        await deleteSession(settings, sessionId);
        setSessions((current) => current.filter((s) => s.id !== sessionId));
        if (activeSessionId === sessionId) {
          const remaining = sessions.filter((s) => s.id !== sessionId);
          if (remaining.length > 0) {
            setActiveSessionId(remaining[0].id);
          } else {
            setActiveSessionId("");
            setBackendSessionId("");
            setMessages([]);
          }
        }
      } catch (error) {
        console.error("Failed to delete session:", error);
      }
    }
  }

  async function handleRenameSession(sessionId: string, newTitle: string) {
    if (settings.mode === "backend") {
      try {
        await renameSession(settings, sessionId, newTitle);
        setSessions((current) =>
          current.map((s) => s.id === sessionId ? { ...s, title: newTitle } : s)
        );
      } catch (error) {
        console.error("Failed to rename session:", error);
      }
    }
  }

  async function handlePinSession(sessionId: string, setPin: boolean) {
    if (settings.mode === "backend") {
      try {
        await pinSession(settings, sessionId, setPin);
        setSessions((current) =>
          current.map((s) => s.id === sessionId ? { ...s, pinned: setPin } : s)
        );
      } catch (error) {
        console.error("Failed to pin session:", error);
      }
    }
  }

  async function handleDeleteAllSessions() {
    if (settings.mode === "backend") {
      try {
        await Promise.all(sessions.map((s) => deleteSession(settings, s.id)));
        setSessions([]);
        setActiveSessionId("");
        setBackendSessionId("");
        setMessages([]);
      } catch (error) {
        console.error("Failed to delete all sessions:", error);
      }
    }
  }

  async function handleCreateCustomSearchCredential(provider: string, apiKey: string) {
    if (settings.mode === "mock") {
      const now = new Date().toISOString();
      const credential: WebSearchCredential = {
        user_id: settings.userId,
        provider,
        source: "custom",
        is_member: false,
        api_key_masked: maskApiKey(apiKey),
        api_key_fingerprint: `mock-${provider}-${Date.now()}`,
        is_active: true,
        created_at: now,
        updated_at: now,
      };

      setMockSearchCredentialState((current) => {
        const withoutSameProvider = current.filter((item) => {
          return !(item.source === "custom" && item.provider === provider);
        });

        return [
          ...withoutSameProvider.map((item) => (
            { ...item, is_active: false }
          )),
          credential,
        ];
      });
      setSettings((current) => ({
        ...current,
        searchProvider: credential.provider,
        searchSource: credential.source,
      }));

      return credential;
    }

    const credential = await createWebSearchCredential(settings, provider, apiKey);
    const credentials = await listWebSearchCredentials(settings);
    applySearchCredentials(credentials);
    return credential;
  }

  async function handleSelectSearchCredential(source: "platform" | "custom", provider: string) {
    if (settings.mode === "mock") {
      const now = new Date().toISOString();
      const existingCredential = mockSearchCredentialState.find((credential) => {
        return credential.source === source && credential.provider === provider;
      });
      const selectedCredential: WebSearchCredential = existingCredential
        ? {
          ...existingCredential,
          is_active: true,
          updated_at: now,
        }
        : {
          user_id: settings.userId,
          provider,
          source,
          is_member: source === "platform",
          api_key_masked: "",
          api_key_fingerprint: "",
          is_active: true,
          created_at: now,
          updated_at: now,
        };

      setMockSearchCredentialState((current) => current.map((credential) => {
        if (credential.source !== source || credential.provider !== provider) {
          return { ...credential, is_active: false };
        }

        return selectedCredential;
      }));

      setSettings((current) => ({
        ...current,
        searchProvider: selectedCredential.provider,
        searchSource: selectedCredential.source,
      }));

      return selectedCredential;
    }

    const credential = await setActiveWebSearchCredential(settings, source, provider);
    const credentials = await listWebSearchCredentials(settings);
    applySearchCredentials(credentials);
    return credential;
  }

  async function handleSetPlatformMembership(isMember: boolean) {
    if (settings.mode === "mock") {
      const now = new Date().toISOString();
      const credential: WebSearchCredential = {
        user_id: settings.userId,
        provider: isMember ? "exa" : "4get_ddg",
        source: "platform",
        is_member: isMember,
        api_key_masked: "",
        api_key_fingerprint: "",
        is_active: true,
        created_at: now,
        updated_at: now,
      };

      setMockSearchCredentialState((current) => [
        credential,
        ...current.filter((item) => item.source !== "platform"),
      ]);
      setSettings((current) => ({
        ...current,
        searchProvider: credential.provider,
        searchSource: credential.source,
      }));

      return credential;
    }

    const credential = await setPlatformMembership(settings, isMember);
    const credentials = await listWebSearchCredentials(settings);
    applySearchCredentials(credentials);
    return credential;
  }

  return (
    <AppErrorBoundary>
      <div className="app-shell h-screen overflow-hidden">
        <div className="relative z-10 flex h-full gap-6 p-3 md:p-5 xl:gap-8 xl:p-6">
          <Sidebar
            activeSessionId={activeSessionId}
            mobileOpen={mobileSidebarOpen}
            onCreateSession={handleCreateSession}
            onDeleteSession={handleDeleteSession}
            onDeleteAllSessions={handleDeleteAllSessions}
            onMobileOpenChange={setMobileSidebarOpen}
            onOpenSettings={() => setSettingsOpen(true)}
            onPinSession={handlePinSession}
            onRenameSession={handleRenameSession}
            onSelectSession={setActiveSessionId}
            sessions={sessions}
          />

          <div className="flex min-h-0 min-w-0 flex-1 justify-center px-3 md:px-8 xl:px-12">
            <div className="flex min-h-0 w-full max-w-[860px] flex-col gap-3 2xl:max-w-[920px]">
              <div className="min-h-0 flex-1">
                <ChatWindow messages={displayedMessages} status={settings.mode === "mock" ? "ready" : status} />
              </div>

              <div className="relative shrink-0 pt-2 before:absolute before:inset-x-0 before:-top-8 before:h-8 before:bg-gradient-to-t before:from-[#f7f8fa] before:to-transparent before:content-['']">
                <InputBox
                  modelLoadError={displayedModelLoadError}
                  modelOptions={displayedModelOptions}
                  onChange={setInput}
                  onCreateCustomSearchCredential={handleCreateCustomSearchCredential}
                  onSelectModel={(option) => {
                    setSettings((current) => ({
                      ...current,
                      modelId: option.modelId,
                      providerId: option.providerId,
                    }));
                  }}
                  onSelectSearchCredential={handleSelectSearchCredential}
                  onSetPlatformMembership={handleSetPlatformMembership}
                  onStop={stop}
                  onSubmit={handleSubmit}
                  searchCredentials={displayedSearchCredentials}
                  settings={settings}
                  status={settings.mode === "mock" ? "ready" : status}
                  value={input}
                />
              </div>
            </div>
          </div>
        </div>

        <SettingsDialog
          modelLoadError={displayedModelLoadError}
          modelOptions={displayedModelOptions}
          onChange={setSettings}
          onSelectMockMode={() => {
            const activeCustom = mockSearchCredentialState.find((credential) => {
              return credential.source === "custom" && credential.is_active;
            });
            const platform = mockSearchCredentialState.find((credential) => credential.source === "platform");
            const selectedSearchCredential = activeCustom ?? platform;
            const selectedModel = mockModelOptions[0];

            setSettings((current) => ({
              ...current,
              mode: "mock",
              modelId: selectedModel.modelId,
              providerId: selectedModel.providerId,
              searchProvider: selectedSearchCredential?.provider ?? current.searchProvider,
              searchSource: selectedSearchCredential?.source ?? current.searchSource,
            }));
          }}
          onCreateCustomSearchCredential={handleCreateCustomSearchCredential}
          onOpenChange={setSettingsOpen}
          onSelectSearchCredential={handleSelectSearchCredential}
          onSetPlatformMembership={handleSetPlatformMembership}
          open={settingsOpen}
          searchCredentials={displayedSearchCredentials}
          searchLoadError={displayedSearchLoadError}
          settings={settings}
        />
      </div>
    </AppErrorBoundary>
  );
}
