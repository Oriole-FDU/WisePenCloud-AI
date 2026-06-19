import * as Dialog from "@radix-ui/react-dialog";
import * as Select from "@radix-ui/react-select";
import { CheckCircle2, ChevronDown, Crown, DatabaseZap, KeyRound, Search, X } from "lucide-react";
import type { ChangeEvent } from "react";
import { useState } from "react";
import type { ModelOption, RuntimeSettings, WebSearchCredential } from "../types/chat";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";

type SettingsDialogProps = {
  open: boolean;
  settings: RuntimeSettings;
  modelOptions: ModelOption[];
  modelLoadError: string;
  searchCredentials: WebSearchCredential[];
  searchLoadError: string;
  onOpenChange: (open: boolean) => void;
  onChange: (settings: RuntimeSettings | ((prev: RuntimeSettings) => RuntimeSettings)) => void;
  onSelectMockMode: () => void;
  onSetPlatformMembership: (isMember: boolean) => Promise<WebSearchCredential>;
  onSelectSearchCredential: (source: "platform" | "custom", provider: string) => Promise<WebSearchCredential>;
  onCreateCustomSearchCredential: (provider: string, apiKey: string) => Promise<WebSearchCredential>;
};

const CUSTOM_SEARCH_PROVIDERS = [
  { value: "exa", label: "Exa" },
  { value: "tavily", label: "Tavily" },
  { value: "anysearch", label: "AnySearch" },
  { value: "serper", label: "Serper" },
];

function setField<T extends keyof RuntimeSettings>(
  settings: RuntimeSettings,
  key: T,
  value: RuntimeSettings[T],
) {
  return {
    ...settings,
    [key]: value,
  };
}

export function SettingsDialog({
  open,
  settings,
  modelOptions,
  modelLoadError,
  searchCredentials,
  searchLoadError,
  onOpenChange,
  onChange,
  onSelectMockMode,
  onSetPlatformMembership,
  onSelectSearchCredential,
  onCreateCustomSearchCredential,
}: SettingsDialogProps) {
  const [customProvider, setCustomProvider] = useState("exa");
  const [customApiKey, setCustomApiKey] = useState("");
  const [savingSearch, setSavingSearch] = useState(false);
  const [saveMessage, setSaveMessage] = useState("");
  const selectedModelValue = `${settings.modelId}:${settings.providerId}`;
  const platformCredential = searchCredentials.find((credential) => credential.source === "platform");
  const activeCustomCredential = searchCredentials.find((credential) => {
    return credential.source === "custom" && credential.is_active;
  });

  function handleInput(key: keyof RuntimeSettings) {
    return (event: ChangeEvent<HTMLInputElement>) => {
      onChange(setField(settings, key, event.target.value as RuntimeSettings[typeof key]));
    };
  }

  async function handleMembership() {
    setSavingSearch(true);
    setSaveMessage("");

    try {
      const credential = await onSetPlatformMembership(true);
      onChange((prev: RuntimeSettings) => ({
        ...prev,
        searchProvider: credential.provider,
        searchSource: credential.source,
      }));
      setSaveMessage("会员已开通，平台搜索源已切换到高级源。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "开通会员失败";
      setSaveMessage(message);
    } finally {
      setSavingSearch(false);
    }
  }

  async function handleSelectSearchCredential(source: "platform" | "custom", provider: string) {
    setSavingSearch(true);
    setSaveMessage("");

    try {
      const credential = await onSelectSearchCredential(source, provider);
      onChange((prev: RuntimeSettings) => ({
        ...prev,
        searchProvider: credential.provider,
        searchSource: credential.source,
      }));
      setSaveMessage("搜索源已切换。");
    } catch (error) {
      const message = error instanceof Error ? error.message : "切换搜索源失败";
      setSaveMessage(message);
    } finally {
      setSavingSearch(false);
    }
  }

  async function handleCustomCredential() {
    console.log("handleCustomCredential called", { customProvider, customApiKey: customApiKey ? "filled" : "empty" });
    
    if (!customApiKey.trim()) {
      setSaveMessage("请输入搜索源 API Key。");
      return;
    }

    setSavingSearch(true);
    setSaveMessage("");

    try {
      console.log("Calling onCreateCustomSearchCredential...");
      const credential = await onCreateCustomSearchCredential(customProvider, customApiKey);
      console.log("Credential created:", credential);
      // 使用函数式更新，避免与 applySearchCredentials 的 setSettings 竞争
      onChange((prev: RuntimeSettings) => ({
        ...prev,
        searchProvider: credential.provider,
        searchSource: credential.source,
      }));
      setCustomApiKey("");
      setSaveMessage("自定义搜索源已保存。");
    } catch (error) {
      console.error("handleCustomCredential error:", error);
      const message = error instanceof Error ? error.message : "保存失败";
      setSaveMessage(message);
    } finally {
      setSavingSearch(false);
    }
  }

  return (
    <Dialog.Root onOpenChange={onOpenChange} open={open}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/18 backdrop-blur-sm" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[88vh] w-[min(94vw,780px)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-xl border border-gray-200 bg-white p-5 shadow-2xl outline-none">
          <div className="mb-5 flex items-start justify-between gap-3">
            <div>
              <Dialog.Title className="font-display text-lg font-semibold text-gray-900">
                运行设置
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm font-medium text-gray-600">
                连接后端、选择模型，并管理搜索源与会员状态。
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <IconButton label="关闭设置">
                <X className="h-4 w-4" />
              </IconButton>
            </Dialog.Close>
          </div>

          <div className="space-y-5">
            <section className="rounded-xl border border-gray-200 bg-gray-50/70 p-4 shadow-sm">
              <div className="mb-3 flex items-center gap-2">
                <DatabaseZap className="h-4 w-4 text-sky-500" />
                <h2 className="text-sm font-semibold text-gray-900">对话运行</h2>
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>运行模式</span>
                  <div className="flex gap-2">
                    <Button
                      className="flex-1"
                      onClick={onSelectMockMode}
                      type="button"
                      variant={settings.mode === "mock" ? "primary" : "secondary"}
                    >
                      演示
                    </Button>
                    <Button
                      className="flex-1"
                      onClick={() => onChange(setField(settings, "mode", "backend"))}
                      type="button"
                      variant={settings.mode === "backend" ? "primary" : "secondary"}
                    >
                      后端
                    </Button>
                  </div>
                </label>

                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>模型</span>
                  <Select.Root
                    onValueChange={(value) => {
                      const option = modelOptions.find((item) => item.value === value);

                      if (option) {
                        onChange({
                          ...settings,
                          modelId: option.modelId,
                          providerId: option.providerId,
                        });
                      }
                    }}
                    value={selectedModelValue}
                  >
                    <Select.Trigger className="inline-flex h-11 w-full items-center justify-between rounded-lg border border-gray-200 bg-white px-3 text-sm font-medium text-gray-900 shadow-sm outline-none focus:ring-2 focus:ring-sky-500/20">
                      <Select.Value placeholder={modelLoadError ? "模型加载失败" : "选择模型"} />
                      <Select.Icon>
                        <ChevronDown className="h-4 w-4 text-gray-600" />
                      </Select.Icon>
                    </Select.Trigger>
                    <Select.Portal>
                      <Select.Content className="z-50 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl">
                        <Select.Viewport className="p-1">
                          {modelOptions.map((option) => (
                            <Select.Item
                              className="rounded-md px-3 py-2 text-sm font-medium text-gray-800 outline-none hover:bg-sky-50 data-[highlighted]:bg-sky-50"
                              key={option.value}
                              value={option.value}
                            >
                              <Select.ItemText>
                                <span className="block">{option.label}</span>
                                <span className="block text-xs text-gray-600">{option.description}</span>
                              </Select.ItemText>
                            </Select.Item>
                          ))}
                        </Select.Viewport>
                      </Select.Content>
                    </Select.Portal>
                  </Select.Root>
                  {modelLoadError ? <p className="text-xs font-medium text-red-700">{modelLoadError}</p> : null}
                </label>

                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>后端地址</span>
                  <input
                    className="h-11 w-full rounded-lg border border-gray-200 bg-white px-3 font-mono text-sm text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20"
                    onChange={handleInput("baseUrl")}
                    value={settings.baseUrl}
                  />
                </label>

                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>用户 ID</span>
                  <input
                    className="h-11 w-full rounded-lg border border-gray-200 bg-white px-3 font-mono text-sm text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20"
                    onChange={handleInput("userId")}
                    value={settings.userId}
                  />
                </label>

                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>来源密钥</span>
                  <input
                    className="h-11 w-full rounded-lg border border-gray-200 bg-white px-3 font-mono text-sm text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20"
                    onChange={handleInput("fromSource")}
                    value={settings.fromSource}
                  />
                </label>

                <label className="space-y-1.5 text-sm font-semibold text-gray-700">
                  <span>身份类型</span>
                  <input
                    className="h-11 w-full rounded-lg border border-gray-200 bg-white px-3 font-mono text-sm text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20"
                    onChange={handleInput("identityType")}
                    value={settings.identityType}
                  />
                </label>
              </div>
            </section>

            <section className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <Search className="h-4 w-4 text-sky-500" />
                  <h2 className="text-sm font-semibold text-gray-900">搜索源</h2>
                </div>
                <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-600">
                  {settings.searchSource}:{settings.searchProvider}
                </span>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <p className="text-sm font-semibold text-gray-900">平台搜索</p>
                      <p className="mt-1 text-xs font-medium leading-6 text-gray-600">
                        默认使用 4get + DuckDuckGo，开通会员后切换到 Exa。
                      </p>
                    </div>
                    {platformCredential?.is_member ? (
                      <CheckCircle2 className="h-5 w-5 shrink-0 text-sky-500" />
                    ) : null}
                  </div>
                  <Button
                    className="mt-3 w-full"
                    disabled={savingSearch}
                    onClick={() => {
                      void handleSelectSearchCredential("platform", platformCredential?.provider ?? "4get_ddg");
                    }}
                    type="button"
                    variant={settings.searchSource === "platform" ? "primary" : "secondary"}
                  >
                    使用平台搜索
                  </Button>
                  <Button
                    className="mt-2 w-full"
                    disabled={savingSearch || platformCredential?.is_member}
                    onClick={handleMembership}
                    type="button"
                    variant={platformCredential?.is_member ? "secondary" : "primary"}
                  >
                    <Crown className="h-4 w-4" />
                    {platformCredential?.is_member ? "会员已开通" : "开通会员"}
                  </Button>
                </div>

                <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div className="flex items-center gap-2">
                    <KeyRound className="h-4 w-4 text-sky-500" />
                    <p className="text-sm font-semibold text-gray-900">自定义搜索源</p>
                  </div>
                  <div className="mt-3 grid gap-2">
                    <Select.Root onValueChange={setCustomProvider} value={customProvider}>
                      <Select.Trigger className="inline-flex h-10 w-full items-center justify-between rounded-lg border border-gray-200 bg-white px-3 text-sm font-medium text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20">
                        <Select.Value />
                        <Select.Icon>
                          <ChevronDown className="h-4 w-4 text-gray-600" />
                        </Select.Icon>
                      </Select.Trigger>
                      <Select.Portal>
                        <Select.Content className="z-50 overflow-hidden rounded-lg border border-gray-200 bg-white shadow-xl">
                          <Select.Viewport className="p-1">
                            {CUSTOM_SEARCH_PROVIDERS.map((provider) => (
                              <Select.Item
                                className="rounded-md px-3 py-2 text-sm font-medium text-gray-800 outline-none hover:bg-sky-50 data-[highlighted]:bg-sky-50"
                                key={provider.value}
                                value={provider.value}
                              >
                                <Select.ItemText>{provider.label}</Select.ItemText>
                              </Select.Item>
                            ))}
                          </Select.Viewport>
                        </Select.Content>
                      </Select.Portal>
                    </Select.Root>
                    <input
                      className="h-10 rounded-lg border border-gray-200 bg-white px-3 font-mono text-sm text-gray-900 outline-none placeholder:text-gray-500 focus:ring-2 focus:ring-sky-500/20"
                      onChange={(event) => setCustomApiKey(event.target.value)}
                      placeholder="输入 API Key"
                      type="password"
                      value={customApiKey}
                    />
                    <Button disabled={savingSearch} onClick={handleCustomCredential} type="button">
                      保存自定义源
                    </Button>
                    {activeCustomCredential ? (
                      <div className="flex items-center justify-between gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2">
                        <p className="font-mono text-[11px] font-semibold text-gray-600">
                          {activeCustomCredential.provider} · {activeCustomCredential.api_key_masked}
                        </p>
                        <Button
                          disabled={savingSearch}
                          onClick={() => {
                            void handleSelectSearchCredential("custom", activeCustomCredential.provider);
                          }}
                          size="sm"
                          type="button"
                          variant={settings.searchSource === "custom" ? "primary" : "secondary"}
                        >
                          使用
                        </Button>
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              {saveMessage ? <p className="mt-3 text-sm font-semibold text-sky-500">{saveMessage}</p> : null}
              {searchLoadError ? <p className="mt-3 text-sm font-semibold text-red-700">{searchLoadError}</p> : null}
            </section>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
