import { useEffect, useId, useState } from "react";
import * as Tabs from "@radix-ui/react-tabs";
import * as Tooltip from "@radix-ui/react-tooltip";
import { codeToHtml } from "shiki";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  Copy,
} from "lucide-react";
import { getToolName, isStaticToolUIPart } from "ai";
import JsonView from "react18-json-view";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";
import { Panel } from "./ui/panel";
import { cn, formatDuration, formatTime, tryStringify } from "../lib/utils";
import type { ToolPart } from "../types/chat";

type ToolCallCardProps = {
  part: ToolPart;
  defaultExpanded?: boolean;
};

function deriveStatus(part: ToolPart) {
  switch (part.state) {
    case "input-streaming":
    case "input-available":
    case "approval-requested":
    case "approval-responded":
      return "running";
    case "output-available":
      return "success";
    case "output-error":
    case "output-denied":
      return "error";
    default:
      return "pending";
  }
}

function estimateDuration(part: ToolPart) {
  const startedAt = part.toolMetadata?.started_at;
  const endedAt = part.toolMetadata?.ended_at;

  if (typeof part.toolMetadata?.duration_ms === "number") {
    return part.toolMetadata.duration_ms;
  }

  if (typeof startedAt === "string" && typeof endedAt === "string") {
    return new Date(endedAt).getTime() - new Date(startedAt).getTime();
  }

  return undefined;
}

function statusText(status: ReturnType<typeof deriveStatus>) {
  switch (status) {
    case "running":
      return "运行中";
    case "success":
      return "已完成";
    case "error":
      return "失败";
    default:
      return "等待中";
  }
}

function copyText(value: string) {
  void navigator.clipboard.writeText(value);
}

function getToolOutputText(part: ToolPart): string {
  if (!("output" in part)) {
    return "";
  }

  const output = part.output;
  if (output && typeof output === "object" && "output" in output) {
    const payload = output as { output?: unknown };
    return typeof payload.output === "string" ? payload.output : tryStringify(payload.output);
  }

  return typeof output === "string" ? output : tryStringify(output);
}

function getMetadataRecord(part: ToolPart) {
  return {
    toolType: isStaticToolUIPart(part) ? "static tool part" : "dynamic tool part",
    toolCallId: part.toolCallId,
    state: part.state,
    startedAt: typeof part.toolMetadata?.started_at === "string" ? part.toolMetadata.started_at : undefined,
    endedAt: typeof part.toolMetadata?.ended_at === "string" ? part.toolMetadata.ended_at : undefined,
    durationMs: estimateDuration(part),
    providerExecuted: "providerExecuted" in part ? part.providerExecuted : undefined,
    ...part.toolMetadata,
  };
}

export function ToolCallCard({ part, defaultExpanded = false }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const [copied, setCopied] = useState(false);
  const [renderedCode, setRenderedCode] = useState({ source: "", html: "" });
  const status = deriveStatus(part);
  const [liveDuration, setLiveDuration] = useState<number | undefined>(() => {
    return status === "running" ? 0 : estimateDuration(part);
  });
  const toolName = getToolName(part);
  const contentId = useId();
  const outputText = getToolOutputText(part);
  const inputText = tryStringify(part.input);
  const metadataRecord = getMetadataRecord(part);
  const metadataText = tryStringify(metadataRecord);
  const rawPayload = tryStringify({
    type: part.type,
    toolCallId: part.toolCallId,
    toolName,
    state: part.state,
    input: part.input,
    output: "output" in part ? part.output : undefined,
    errorText: "errorText" in part ? part.errorText : undefined,
    toolMetadata: part.toolMetadata,
  });

  useEffect(() => {
    if (status !== "running") {
      setLiveDuration(estimateDuration(part));
      return;
    }

    const startedAt = typeof part.toolMetadata?.started_at === "string"
      ? new Date(part.toolMetadata.started_at).getTime()
      : Date.now();

    const timer = window.setInterval(() => {
      setLiveDuration(Date.now() - startedAt);
    }, 120);

    return () => window.clearInterval(timer);
  }, [part, status]);

  useEffect(() => {
    let active = true;

    if (!outputText) {
      setRenderedCode({ source: "", html: "" });
      return;
    }

    void codeToHtml(outputText, {
      lang: "xml",
      theme: "github-light",
    }).then((html) => {
      if (active) {
        setRenderedCode({ source: outputText, html });
      }
    }).catch(() => {
      if (active) {
        setRenderedCode({ source: "", html: "" });
      }
    });

    return () => {
      active = false;
    };
  }, [outputText]);

  const handleCopy = () => {
    copyText(rawPayload);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <Panel
      className={cn(
        "w-full overflow-hidden rounded-2xl border shadow-[0_12px_28px_rgba(15,23,42,0.08)] transition-all duration-200",
        "hover:-translate-y-0.5 hover:shadow-[0_20px_40px_rgba(15,23,42,0.12)]",
        status === "error"
          ? "border-red-300 bg-white"
          : "border-emerald-300 bg-white",
      )}
    >
      <button
        aria-controls={contentId}
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-start gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/25 focus-visible:ring-inset",
          status === "error" ? "bg-red-50/70 hover:bg-red-50" : "bg-emerald-50/80 hover:bg-emerald-50",
        )}
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <div
          className={cn(
            "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border",
            status === "error"
              ? "border-red-200 bg-red-100 text-red-700"
              : status === "success"
                ? "border-emerald-200 bg-emerald-100 text-emerald-700"
                : "border-sky-200 bg-sky-100 text-sky-700",
          )}
        >
          {status === "error" ? (
            <AlertTriangle className="h-3.5 w-3.5" />
          ) : status === "success" ? (
            <CheckCircle2 className="h-3.5 w-3.5" />
          ) : (
            <span className="dashed-spinner h-3.5 w-3.5" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-[14px] font-semibold text-slate-900">
              {toolName}
            </span>
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-[11px] font-medium",
                status === "error"
                  ? "bg-red-100 text-red-700"
                  : "bg-emerald-100 text-emerald-700",
              )}
            >
              {statusText(status)}
            </span>
            <span className="font-mono text-[11px] font-semibold tabular-nums text-slate-500">
              {formatDuration(liveDuration)}
            </span>
          </div>
        </div>

        <div className="flex items-center gap-1.5 pt-0.5">
          <Tooltip.Provider delayDuration={150}>
            <Tooltip.Root>
              <Tooltip.Trigger asChild>
                <IconButton
                  className="h-8 w-8 rounded-full border border-transparent bg-white/65 text-slate-500 shadow-sm hover:border-slate-200 hover:bg-white"
                  label="复制原始请求体"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleCopy();
                  }}
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-emerald-600" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </IconButton>
              </Tooltip.Trigger>
              <Tooltip.Portal>
                <Tooltip.Content
                  className="rounded-lg border border-gray-200 bg-white px-2.5 py-1.5 text-xs font-medium text-gray-700 shadow-lg"
                  sideOffset={8}
                >
                  复制原始请求体
                </Tooltip.Content>
              </Tooltip.Portal>
            </Tooltip.Root>
          </Tooltip.Provider>

          <span
            className={cn(
              "flex h-8 w-8 items-center justify-center rounded-full text-slate-400 transition-transform duration-200",
              expanded && "rotate-180",
            )}
          >
            <ChevronDown className="h-4 w-4" />
          </span>
        </div>
      </button>

      <div
        className="grid transition-[grid-template-rows] duration-300 ease-out"
        style={{ gridTemplateRows: expanded ? "1fr" : "0fr" }}
      >
        <div className="overflow-hidden">
          <div
            className={cn(
              "border-t px-4 pb-4 pt-3",
              status === "error" ? "border-red-200/80" : "border-emerald-200/80",
            )}
            id={contentId}
          >
            <Tabs.Root className="flex flex-col gap-4" defaultValue="output">
              <Tabs.List className="inline-flex w-fit rounded-xl border border-slate-200 bg-slate-100/90 p-1 shadow-sm">
                <Tabs.Trigger
                  className="rounded-lg px-3 py-1.5 text-sm font-semibold text-slate-600 outline-none transition-colors data-[state=active]:bg-white data-[state=active]:text-sky-700 data-[state=active]:shadow-sm focus-visible:ring-2 focus-visible:ring-sky-500/25"
                  value="input"
                >
                  Input
                </Tabs.Trigger>
                <Tabs.Trigger
                  className="rounded-lg px-3 py-1.5 text-sm font-semibold text-slate-600 outline-none transition-colors data-[state=active]:bg-white data-[state=active]:text-sky-700 data-[state=active]:shadow-sm focus-visible:ring-2 focus-visible:ring-sky-500/25"
                  value="output"
                >
                  Output
                </Tabs.Trigger>
                <Tabs.Trigger
                  className="rounded-lg px-3 py-1.5 text-sm font-semibold text-slate-600 outline-none transition-colors data-[state=active]:bg-white data-[state=active]:text-sky-700 data-[state=active]:shadow-sm focus-visible:ring-2 focus-visible:ring-sky-500/25"
                  value="metadata"
                >
                  Metadata
                </Tabs.Trigger>
              </Tabs.List>

              <Tabs.Content className="mt-0" value="input">
                <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-700">
                      Input
                    </p>
                    <Button size="sm" variant="ghost" onClick={() => copyText(inputText)}>
                      <Copy className="h-3.5 w-3.5" />
                      复制
                    </Button>
                  </div>
                  <div className="json-tree-panel rounded-xl border border-slate-200 bg-white p-4">
                    <JsonView collapsed={2} enableClipboard src={part.input} theme="github" />
                  </div>
                </div>
              </Tabs.Content>

              <Tabs.Content className="mt-0" value="output">
                {"output" in part ? (
                  <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-700">
                        Output
                      </p>
                      <Button size="sm" variant="ghost" onClick={() => copyText(outputText)}>
                        <Copy className="h-3.5 w-3.5" />
                        复制
                      </Button>
                    </div>
                    <div className="rendered-code-panel scrollbar-thin max-h-[460px] overflow-auto rounded-xl border border-slate-200 bg-white">
                      {renderedCode.source === outputText && renderedCode.html ? (
                        <div dangerouslySetInnerHTML={{ __html: renderedCode.html }} />
                      ) : outputText ? (
                        <pre className="whitespace-pre-wrap px-5 py-4 font-mono text-[13px] leading-7 text-slate-800">
                          {outputText}
                        </pre>
                      ) : (
                        <p className="px-5 py-4 text-sm text-slate-500">暂无输出内容</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="rounded-xl border border-red-200 bg-red-50/80 p-4">
                    <div className="mb-3 flex items-center justify-between gap-3">
                      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-red-700">
                        Output
                      </p>
                      <Button size="sm" variant="ghost" onClick={() => copyText(part.errorText ?? "")}>
                        <Copy className="h-3.5 w-3.5" />
                        复制
                      </Button>
                    </div>
                    <pre className="scrollbar-thin overflow-auto whitespace-pre-wrap rounded-xl border border-red-100 bg-white px-5 py-4 font-mono text-[13px] leading-7 text-red-900">
                      {part.errorText}
                    </pre>
                  </div>
                )}
              </Tabs.Content>

              <Tabs.Content className="mt-0" value="metadata">
                <div className="rounded-xl border border-slate-200 bg-slate-50/80 p-4">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <p className="text-xs font-semibold uppercase tracking-[0.08em] text-slate-700">
                      Metadata
                    </p>
                    <Button size="sm" variant="ghost" onClick={() => copyText(metadataText)}>
                      <Copy className="h-3.5 w-3.5" />
                      复制
                    </Button>
                  </div>
                  <div className="grid gap-3 lg:grid-cols-2">
                    <div className="rounded-xl border border-slate-200 bg-white p-4">
                      <dl className="space-y-2.5">
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">tool_type</dt>
                          <dd className="font-mono text-[13px] font-semibold leading-6 text-slate-800">{metadataRecord.toolType}</dd>
                        </div>
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">tool_call_id</dt>
                          <dd className="break-words font-mono text-[13px] font-semibold leading-6 text-slate-800">{metadataRecord.toolCallId}</dd>
                        </div>
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">state</dt>
                          <dd className="font-mono text-[13px] font-semibold leading-6 text-slate-800">{metadataRecord.state}</dd>
                        </div>
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">started_at</dt>
                          <dd className="font-mono text-[13px] font-semibold leading-6 text-slate-800">
                            {metadataRecord.startedAt ? formatTime(metadataRecord.startedAt) : "—"}
                          </dd>
                        </div>
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">ended_at</dt>
                          <dd className="font-mono text-[13px] font-semibold leading-6 text-slate-800">
                            {metadataRecord.endedAt ? formatTime(metadataRecord.endedAt) : status === "running" ? "running" : "—"}
                          </dd>
                        </div>
                        <div className="grid grid-cols-[92px_minmax(0,1fr)] gap-3">
                          <dt className="font-mono text-[12px] font-semibold text-slate-500">duration</dt>
                          <dd className="font-mono text-[13px] font-semibold leading-6 text-slate-800">
                            {typeof liveDuration === "number" ? formatDuration(liveDuration) : "—"}
                          </dd>
                        </div>
                      </dl>
                    </div>

                    <div className="json-tree-panel rounded-xl border border-slate-200 bg-white p-4">
                      <JsonView collapsed={2} enableClipboard src={metadataRecord} theme="github" />
                    </div>
                  </div>
                </div>
              </Tabs.Content>
            </Tabs.Root>
          </div>
        </div>
      </div>
    </Panel>
  );
}
