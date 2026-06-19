import { useState } from "react";
import {
  Search,
  Globe,
  FileText,
  FileSearch,
  Check,
  Copy,
  ChevronDown,
  Bot,
  Sparkles,
} from "lucide-react";

const TOOL_META = {
  web_search: { icon: Search, label: "web_search" },
  web_fetch: { icon: Globe, label: "web_fetch" },
  document_parse: { icon: FileText, label: "document_parse" },
  tool_content_read: { icon: FileSearch, label: "tool_content_read" },
};

const SEARCH_QUERIES = [
  "GPT-4o 代码生成基准测试",
  "Claude 3.5 Sonnet reasoning evaluation",
  "多模态模型基准对比 2026",
  "LLM coding benchmark leaderboard",
  "AI 模型推理速度对比",
  "function calling 准确率评测",
  "long context 检索能力测试",
  "RAG 检索增强生成 评测",
  "embedding 模型基准对比",
];

const READ_SOURCES_4 = [
  "candidate #1 · official-blog.com",
  "candidate #2 · arxiv.org",
  "candidate #3 · github.com/openai",
  "candidate #4 · news.ycombinator.com",
];

const READ_SOURCES_3 = [
  "candidate #1 · docs.anthropic.com",
  "candidate #2 · medium.com",
  "candidate #3 · reuters.com",
];

const STEPS = [
  {
    id: 1,
    tool: "web_search",
    type: "group",
    count: 9,
    items: SEARCH_QUERIES.map((q, i) => ({
      id: i,
      label: q,
      duration: (0.4 + Math.random() * 1.4).toFixed(1),
    })),
  },
  {
    id: 2,
    tool: "web_fetch",
    type: "single",
    duration: "7.0",
    preview: { url: "openai.com/index/gpt-4o", status: 200, bytes: "284 KB" },
  },
  {
    id: 3,
    tool: "document_parse",
    type: "single",
    duration: "18",
    preview: { format: "pdf", pages: 12, ocr: false },
  },
  {
    id: 4,
    tool: "tool_content_read",
    type: "group",
    count: 4,
    items: READ_SOURCES_4.map((s, i) => ({
      id: i,
      label: s,
      duration: (0.2 + Math.random() * 0.6).toFixed(1),
    })),
  },
  {
    id: 5,
    tool: "web_fetch",
    type: "single",
    duration: "3.8",
    preview: { url: "arxiv.org/abs/2410.xxxxx", status: 200, bytes: "61 KB" },
  },
  {
    id: 6,
    tool: "document_parse",
    type: "single",
    duration: "9.8",
    preview: { format: "docx", pages: 6, ocr: false },
  },
  {
    id: 7,
    tool: "tool_content_read",
    type: "group",
    count: 3,
    items: READ_SOURCES_3.map((s, i) => ({
      id: i,
      label: s,
      duration: (0.2 + Math.random() * 0.6).toFixed(1),
    })),
  },
];

function StepCard({ step, isLast }) {
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const meta = TOOL_META[step.tool];
  const Icon = meta.icon;
  const isGroup = step.type === "group";

  const toggle = () => setOpen((v) => !v);

  const handleKeyDown = (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle();
    }
  };

  const handleCopy = (e) => {
    e.stopPropagation();
    const text = isGroup
      ? step.items.map((it) => it.label).join("\n")
      : JSON.stringify(step.preview, null, 2);
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };

  return (
    <div className="relative flex gap-3">
      <div className="relative flex w-6 shrink-0 flex-col items-center">
        <div
          className={
            "relative z-10 flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ring-1" +
            (isGroup
              ? " bg-violet-100 text-violet-700 ring-violet-300"
              : " bg-emerald-50 text-emerald-600 ring-emerald-200")
          }
        >
          {isGroup ? step.count : <Check className="h-3.5 w-3.5" />}
        </div>
        {!isLast && <div className="mt-1 w-px flex-1 bg-slate-200" />}
      </div>

      <div className="flex-1 pb-3">
        <div
          role="button"
          tabIndex={0}
          onClick={toggle}
          onKeyDown={handleKeyDown}
          className={
            "flex w-full cursor-pointer items-center justify-between rounded-xl border px-3.5 py-2.5 text-left transition-all hover:-translate-y-0.5 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300" +
            (isGroup
              ? " border-violet-200 bg-violet-50 hover:bg-violet-50"
              : " border-slate-200 bg-white hover:border-slate-300")
          }
        >
          <span className="flex min-w-0 items-center gap-2.5">
            <Icon
              className={
                "h-4 w-4 shrink-0" + (isGroup ? " text-violet-500" : " text-slate-400")
              }
            />
            <span className="truncate font-mono text-sm text-slate-700">{meta.label}</span>
            {isGroup && (
              <span className="rounded-full bg-violet-100 px-1.5 py-0.5 text-xs font-medium text-violet-600">
                x{step.count}
              </span>
            )}
          </span>

          <span className="flex items-center gap-2 pl-2">
            {step.duration && (
              <span className="rounded-md border border-slate-100 bg-slate-50 px-1.5 py-0.5 font-mono text-xs text-slate-400">
                {step.duration}s
              </span>
            )}
            <button
              type="button"
              onClick={handleCopy}
              aria-label="复制"
              className="rounded-md p-1 text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-300"
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 text-emerald-500" />
              ) : (
                <Copy className="h-3.5 w-3.5" />
              )}
            </button>
            <ChevronDown
              className={
                "h-4 w-4 text-slate-400 transition-transform duration-200" +
                (open ? " rotate-180" : "")
              }
            />
          </span>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateRows: open ? "1fr" : "0fr",
            transition: "grid-template-rows 280ms ease",
          }}
        >
          <div className="overflow-hidden">
            <div className="mt-1.5 rounded-lg border border-slate-100 bg-slate-50 p-3">
              {isGroup ? (
                <ul className="space-y-1.5">
                  {step.items.map((it) => (
                    <li
                      key={it.id}
                      className="flex items-center justify-between gap-3 rounded-md border border-slate-100 bg-white px-2.5 py-1.5"
                    >
                      <span className="truncate font-mono text-xs text-slate-600">
                        {it.label}
                      </span>
                      <span className="shrink-0 font-mono text-xs text-slate-400">
                        {it.duration}s
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-xs text-slate-500">
                  {JSON.stringify(step.preview, null, 2)}
                </pre>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ToolCallTimeline() {
  return (
    <div className="w-full bg-slate-50 p-6">
      <div className="mx-auto flex max-w-xl items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-white shadow-sm ring-1 ring-slate-200">
          <Bot className="h-4 w-4 text-slate-500" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="mb-3 flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 shadow-sm">
            <Sparkles className="h-4 w-4 text-indigo-500" />
            <span className="text-sm font-medium text-slate-700">思考完成</span>
            <span className="text-xs text-slate-400">思考已完成</span>
          </div>

          <div className="max-h-96 overflow-y-auto pr-1">
            {STEPS.map((step, i) => (
              <StepCard key={step.id} step={step} isLast={i === STEPS.length - 1} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
