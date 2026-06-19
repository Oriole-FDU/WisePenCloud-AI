import type { KeyboardEvent } from "react";
import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CheckCircle2, ChevronDown, Crown, Paperclip, Plus, Search, Settings2, Sparkles, Square, SendHorizontal } from "lucide-react";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";
import { Markdown } from "./ui/markdown";
import { cn } from "../lib/utils";
import type { ModelOption, RuntimeSettings, WebSearchCredential } from "../types/chat";

type InputBoxProps = {
  value: string;
  settings: RuntimeSettings;
  modelOptions: ModelOption[];
  modelLoadError: string;
  searchCredentials: WebSearchCredential[];
  status: "submitted" | "streaming" | "ready" | "error";
  onChange: (value: string) => void;
  onSelectModel: (option: ModelOption) => void;
  onSelectSearchCredential: (source: "platform" | "custom", provider: string) => Promise<WebSearchCredential>;
  onCreateCustomSearchCredential: (provider: string, apiKey: string) => Promise<WebSearchCredential>;
  onSetPlatformMembership: (isMember: boolean) => Promise<WebSearchCredential>;
  onSubmit: () => void;
  onStop: () => void;
};

function billingRatioText(value: number) {
  return Number.isInteger(value) ? `${value}x` : `${value.toFixed(1)}x`;
}

export function InputBox({
  value,
  settings,
  modelOptions,
  modelLoadError,
  searchCredentials,
  status,
  onChange,
  onSelectModel,
  onSelectSearchCredential,
  onCreateCustomSearchCredential,
  onSetPlatformMembership,
  onSubmit,
  onStop,
}: InputBoxProps) {
  const [settingsExpanded, setSettingsExpanded] = useState(false);
  const [activeSection, setActiveSection] = useState<"search" | "subscription" | "model" | null>(null);
  const [savingSearch, setSavingSearch] = useState(false);
  const [selectedCustomProvider, setSelectedCustomProvider] = useState("exa");
  const [customApiKey, setCustomApiKey] = useState("");
  const selectedModelValue = `${settings.modelId}:${settings.providerId}`;
  const platformCredential = searchCredentials.find((credential) => credential.source === "platform");
  const activeSearchSource = settings.searchSource;
  const selectedModel = modelOptions.find((option) => option.value === selectedModelValue);
  const customProviders = ["exa", "tavily", "anysearch", "serper"];
  const advancedModels = modelOptions.filter((option) => option.billingRatio > 1);
  const basicModels = modelOptions.filter((option) => option.billingRatio <= 1);
  const hasDraft = value.trim().length > 0;
  const modelGroups = [
    {
      title: "高级模型",
      models: advancedModels,
    },
    {
      title: "基础模型",
      models: basicModels,
    },
  ].filter((group) => group.models.length > 0);

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (status !== "streaming" && value.trim()) {
        onSubmit();
      }
    }
  }

  return (
    <div className="relative rounded-[30px] border border-gray-200 bg-white p-2.5 shadow-sm">
      <AnimatePresence initial={false}>
        {settingsExpanded ? (
          <motion.div
            animate={{ opacity: 1, x: 0, y: 0 }}
            className="absolute bottom-0 left-0 z-30 w-[min(82vw,340px)] md:left-[-22rem] md:bottom-2"
            exit={{ opacity: 0, x: 12, y: 4 }}
            initial={{ opacity: 0, x: 12, y: 4 }}
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
          >
            <div className="rounded-2xl border border-gray-200 bg-gray-50 p-1.5 shadow-xl shadow-gray-900/8">
              <div className="space-y-2">
                <button
                  className="flex w-full cursor-not-allowed items-center justify-between rounded-2xl bg-white px-2.5 py-2 text-left text-xs font-semibold text-gray-500 opacity-75 shadow-sm"
                  disabled
                  type="button"
                >
                  <span className="flex items-center gap-2">
                    <Paperclip className="h-4 w-4 text-gray-400" />
                    附件
                  </span>
                  <span className="text-[11px] font-medium text-gray-500">暂未实现</span>
                </button>

                <button
                  className="flex w-full items-center justify-between rounded-xl bg-white px-2.5 py-2 text-left text-xs font-semibold text-gray-900 shadow-sm"
                  onClick={() => setActiveSection((current) => current === "search" ? null : "search")}
                  type="button"
                >
                  <span className="flex items-center gap-2">
                    <Search className="h-4 w-4 text-sky-500" />
                    搜索源
                  </span>
                  <span className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500">
                    {activeSearchSource === "platform" ? "平台搜索" : "自定义搜索"}
                    <ChevronDown className={cn("h-4 w-4 transition-transform", activeSection === "search" && "rotate-180")} />
                  </span>
                </button>
                <AnimatePresence initial={false}>
                  {activeSection === "search" ? (
                    <motion.div
                      animate={{ height: "auto", opacity: 1 }}
                      className="overflow-hidden"
                      exit={{ height: 0, opacity: 0 }}
                      initial={{ height: 0, opacity: 0 }}
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    >
                      <div className="grid gap-1.5 rounded-xl bg-white p-1.5">
                        {platformCredential ? (
                          <button
                            className={cn(
                              "flex h-8 items-center justify-between rounded-xl border px-2.5 text-xs font-semibold transition-colors",
                              activeSearchSource === "platform"
                                ? "border-sky-200 bg-sky-50 text-sky-500"
                                : "border-gray-200 bg-white text-gray-800 hover:bg-gray-50",
                            )}
                            disabled={savingSearch}
                            onClick={() => {
                              setSavingSearch(true);
                              void onSelectSearchCredential("platform", platformCredential.provider).finally(() => {
                                setSavingSearch(false);
                              });
                            }}
                            type="button"
                          >
                            平台搜索
                            {activeSearchSource === "platform" ? <CheckCircle2 className="h-4 w-4" /> : null}
                          </button>
                        ) : null}

                        <div className="grid gap-2 border-t border-gray-100 pt-2">
                          <p className="px-1 text-[11px] font-semibold text-gray-600">自定义搜索源</p>
                          <div className="grid gap-1.5">
                            {customProviders.map((provider) => (
                              <button
                                className={cn(
                                  "h-8 rounded-xl border px-2.5 text-xs font-semibold transition-colors",
                                  selectedCustomProvider === provider
                                    ? "border-sky-200 bg-sky-50 text-sky-500"
                                    : "border-gray-200 bg-white text-gray-800 hover:bg-gray-50",
                                )}
                                key={provider}
                                onClick={() => setSelectedCustomProvider(provider)}
                                type="button"
                              >
                                {provider}
                              </button>
                            ))}
                          </div>
                          <div className="flex gap-1.5">
                            <input
                              aria-label="自定义搜索源 API Key"
                              className="h-8 min-w-0 flex-1 rounded-xl border border-gray-200 bg-white px-2.5 font-mono text-xs font-semibold text-gray-900 outline-none focus:ring-2 focus:ring-sky-500/20"
                              onChange={(event) => setCustomApiKey(event.target.value)}
                              type="password"
                              value={customApiKey}
                            />
                            <button
                              aria-label="启用自定义搜索源"
                              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-sky-200 bg-sky-500 text-white transition-colors hover:bg-sky-600 disabled:pointer-events-none disabled:opacity-50"
                              disabled={savingSearch || !customApiKey.trim()}
                              onClick={() => {
                                setSavingSearch(true);
                                void onCreateCustomSearchCredential(selectedCustomProvider, customApiKey).then(() => {
                                  setCustomApiKey("");
                                }).finally(() => {
                                  setSavingSearch(false);
                                });
                              }}
                              type="button"
                            >
                              <CheckCircle2 className="h-4 w-4" />
                            </button>
                          </div>
                        </div>
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>

                <button
                  className="flex w-full items-center justify-between rounded-xl bg-white px-2.5 py-2 text-left text-xs font-semibold text-gray-900 shadow-sm"
                  onClick={() => setActiveSection((current) => current === "subscription" ? null : "subscription")}
                  type="button"
                >
                  <span className="flex items-center gap-2">
                    <Crown className="h-4 w-4 text-sky-500" />
                    订阅
                  </span>
                  <span className="flex items-center gap-1.5 text-[11px] font-medium text-gray-500">
                    {platformCredential?.is_member ? "已订阅" : "未订阅"}
                    <ChevronDown className={cn("h-4 w-4 transition-transform", activeSection === "subscription" && "rotate-180")} />
                  </span>
                </button>
                <AnimatePresence initial={false}>
                  {activeSection === "subscription" ? (
                    <motion.div
                      animate={{ height: "auto", opacity: 1 }}
                      className="overflow-hidden"
                      exit={{ height: 0, opacity: 0 }}
                      initial={{ height: 0, opacity: 0 }}
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    >
                      <div className="rounded-xl bg-white p-1.5">
                        <Button
                          className="h-8 w-full text-xs"
                          disabled={savingSearch}
                          onClick={() => {
                            setSavingSearch(true);
                            void onSetPlatformMembership(!platformCredential?.is_member).finally(() => {
                              setSavingSearch(false);
                            });
                          }}
                          type="button"
                          variant={platformCredential?.is_member ? "secondary" : "primary"}
                        >
                          <Crown className="h-4 w-4" />
                          {platformCredential?.is_member ? "取消订阅" : "开通订阅"}
                        </Button>
                        <p className="mt-1.5 px-1 text-[11px] font-medium leading-4 text-gray-500">
                          当前仅用于启用平台高级搜索能力。
                        </p>
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>

                <button
                  className="flex w-full items-center justify-between rounded-xl bg-white px-2.5 py-2 text-left text-xs font-semibold text-gray-900 shadow-sm"
                  onClick={() => setActiveSection((current) => current === "model" ? null : "model")}
                  type="button"
                >
                  <span className="flex items-center gap-2">
                    <Sparkles className="h-4 w-4 text-sky-500" />
                    模型
                  </span>
                    <span className="flex min-w-0 items-center gap-1.5 text-[11px] font-medium text-gray-500">
                      <span className="max-w-32 truncate">{selectedModel?.label ?? (modelLoadError ? "加载失败" : "未选择")}</span>
                      {selectedModel ? (
                        <span className="rounded-full bg-sky-50 px-1.5 py-0.5 font-mono text-[10px] font-bold text-sky-500">
                          {billingRatioText(selectedModel.billingRatio)}
                        </span>
                      ) : null}
                      <ChevronDown className={cn("h-4 w-4 shrink-0 transition-transform", activeSection === "model" && "rotate-180")} />
                    </span>
                </button>
                <AnimatePresence initial={false}>
                  {activeSection === "model" ? (
                    <motion.div
                      animate={{ height: "auto", opacity: 1 }}
                      className="overflow-hidden"
                      exit={{ height: 0, opacity: 0 }}
                      initial={{ height: 0, opacity: 0 }}
                      transition={{ type: "spring", stiffness: 420, damping: 34 }}
                    >
                      <div className="grid gap-2 rounded-xl bg-white p-1.5">
                        {modelGroups.map((group) => (
                          <div className="grid gap-1.5" key={group.title}>
                            <p className="px-1 text-[11px] font-semibold text-gray-600">{group.title}</p>
                            {group.models.map((option) => (
                              <button
                                className={cn(
                                  "rounded-xl border px-2.5 py-2 text-left transition-colors",
                                  option.value === selectedModelValue
                                    ? "border-sky-200 bg-sky-50 text-sky-500"
                                    : "border-gray-200 bg-white text-gray-800 hover:bg-gray-50",
                                )}
                                key={option.value}
                                onClick={() => onSelectModel(option)}
                                type="button"
                              >
                                <span className="flex items-center justify-between gap-2 text-xs font-semibold">
                                  <span className="min-w-0 truncate">{option.label}</span>
                                  <span className="flex shrink-0 items-center gap-1.5">
                                    <span className="rounded-full bg-gray-100 px-1.5 py-0.5 font-mono text-[10px] font-bold text-gray-700">
                                      {billingRatioText(option.billingRatio)}
                                    </span>
                                    {option.value === selectedModelValue ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : null}
                                  </span>
                                </span>
                                <span className="mt-0.5 block text-[11px] font-medium leading-4 text-gray-600">{option.description}</span>
                              </button>
                            ))}
                          </div>
                        ))}
                        {modelGroups.length === 0 ? (
                          <p className="px-2 py-1.5 text-xs font-medium text-gray-500">
                            {modelLoadError || "暂无可选模型"}
                          </p>
                        ) : null}
                      </div>
                    </motion.div>
                  ) : null}
                </AnimatePresence>
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      <div className="flex items-end gap-2.5">
        <IconButton
          className="h-10 w-10 shrink-0 rounded-full border border-gray-200 bg-white text-gray-700 shadow-sm hover:bg-sky-50 hover:text-sky-500"
          label={settingsExpanded ? "收起对话设置" : "展开对话设置"}
          onClick={() => setSettingsExpanded((current) => !current)}
        >
          {settingsExpanded ? <Settings2 className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
        </IconButton>

        <div className="min-w-0 flex-1 rounded-[26px] border border-gray-200 bg-white px-3 py-2 shadow-inner">
          <div className={cn("flex gap-2", hasDraft ? "items-start" : "items-center")}>
            <textarea
              aria-label="消息输入框"
              className={cn(
                "scrollbar-thin min-w-0 flex-1 resize-none border-0 bg-transparent text-sm font-medium leading-6 text-gray-900 outline-none placeholder:text-gray-500",
                hasDraft ? "max-h-40 min-h-[84px]" : "h-8 min-h-8 overflow-hidden py-1",
              )}
              onChange={(event) => onChange(event.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="输入消息"
              rows={hasDraft ? 3 : 1}
              value={value}
            />
            {!hasDraft ? (
              <div className="flex shrink-0 items-center">
                <Button
                  aria-label={status === "streaming" ? "停止生成" : "发送消息"}
                  className={cn(
                    "h-9 w-9 rounded-full border-0 p-0",
                    status === "streaming" ? "bg-red-50 text-red-700 hover:bg-red-100" : "bg-gray-900 text-white hover:bg-gray-800",
                  )}
                  onClick={status === "streaming" ? onStop : onSubmit}
                  variant={status === "streaming" ? "danger" : "primary"}
                >
                  {status === "streaming" ? <Square className="h-4 w-4" /> : <SendHorizontal className="h-4 w-4 -rotate-45" />}
                </Button>
              </div>
            ) : null}
          </div>
          {hasDraft ? (
            <div className="mt-3 rounded-xl border border-gray-200 bg-gray-50/80 px-3 py-2">
              <p className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-gray-600">
                Markdown 预览
              </p>
              <Markdown className="input-markdown-preview">{value}</Markdown>
            </div>
          ) : null}
          {hasDraft ? (
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-gray-100 pt-2">
              <p className="text-xs font-medium text-gray-600">Enter 发送 · Shift + Enter 换行</p>
              <div className="flex items-center">
                <Button
                  aria-label={status === "streaming" ? "停止生成" : "发送消息"}
                  className={cn(
                    "h-10 w-10 rounded-full border-0 p-0",
                    status === "streaming" ? "bg-red-50 text-red-700 hover:bg-red-100" : "bg-gray-900 text-white hover:bg-gray-800",
                  )}
                  onClick={status === "streaming" ? onStop : onSubmit}
                  variant={status === "streaming" ? "danger" : "primary"}
                >
                  {status === "streaming" ? <Square className="h-4 w-4" /> : <SendHorizontal className="h-4 w-4 -rotate-45" />}
                </Button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
