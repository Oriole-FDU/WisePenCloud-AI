import { useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  Bot,
  BrainCircuit,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  RefreshCcw,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { getToolName, isReasoningUIPart, isTextUIPart, isToolUIPart } from "ai";
import type { ChatAppMessage } from "../types/chat";
import type { ToolPart } from "../types/chat";
import { cn, formatDuration, formatTime } from "../lib/utils";
import { Markdown } from "./ui/markdown";
import { IconButton } from "./ui/icon-button";
import { ToolCallCard } from "./ToolCallCard";

type MessageBubbleProps = {
  message: ChatAppMessage;
  isStreaming?: boolean;
  expandAllTools?: boolean;
};

type ReasoningBlockProps = {
  messageId: string;
  parts: Array<{ text: string }>;
  hasTextOutput: boolean;
  toolParts: ToolPart[];
};

type ToolGroup = {
  toolName: string;
  parts: ToolPart[];
};

function deriveToolStatus(part: ToolPart) {
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

function deriveThinkingState(toolParts: ToolPart[], hasTextOutput: boolean) {
  const hasToolError = toolParts.some((part) => deriveToolStatus(part) === "error");
  const hasRunningTool = toolParts.some((part) => deriveToolStatus(part) === "running");

  if (hasToolError) {
    return "error";
  }

  if (hasRunningTool || !hasTextOutput) {
    return "running";
  }

  return "success";
}

function ReasoningBlock({ messageId, parts, hasTextOutput, toolParts }: ReasoningBlockProps) {
  const [expanded, setExpanded] = useState(false);
  const hasContent = parts.some((p) => p.text.trim().length > 0);
  const thinkingState = deriveThinkingState(toolParts, hasTextOutput);
  const hintText = thinkingState === "error"
    ? "工具报错，已停止"
    : thinkingState === "running"
      ? "思考中"
      : "思考已完成";
  const titleText = thinkingState === "error"
    ? "思考中断"
    : thinkingState === "running"
      ? "思考过程"
      : "思考完成";

  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl border shadow-sm",
        thinkingState === "error"
          ? "border-red-200/80 bg-red-50/45"
          : "border-blue-100/60 bg-blue-50/25",
      )}
    >
      <button
        aria-expanded={expanded}
        className={cn(
          "flex w-full items-center justify-between gap-3 border-b border-transparent px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/25 focus-visible:ring-inset",
          hasContent && (thinkingState === "error" ? "hover:bg-red-50/60" : "hover:bg-blue-50/40"),
        )}
        disabled={!hasContent}
        onClick={() => hasContent && setExpanded((current) => !current)}
        type="button"
      >
        <span className="flex min-w-0 items-center gap-2">
          {thinkingState === "running" ? (
            <span className="dashed-spinner h-4 w-4 shrink-0 text-blue-500" />
          ) : thinkingState === "error" ? (
            <AlertTriangle className="h-4 w-4 shrink-0 text-red-600" />
          ) : (
            <BrainCircuit className="h-4 w-4 shrink-0 text-blue-500" />
          )}
          <span className="text-sm font-semibold text-gray-900">{titleText}</span>
          <span className="text-xs font-medium text-gray-500">{hintText}</span>
        </span>
        {hasContent ? (
          expanded ? (
            <ChevronUp className="h-4 w-4 shrink-0 text-gray-600" />
          ) : (
            <ChevronDown className="h-4 w-4 shrink-0 text-gray-600" />
          )
        ) : null}
      </button>

      <AnimatePresence initial={false}>
        {expanded && hasContent ? (
          <motion.div
            animate={{ height: "auto", opacity: 1 }}
            className="overflow-hidden"
            exit={{ height: 0, opacity: 0 }}
            initial={{ height: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 34 }}
          >
            <div
              className={cn(
                "border-t bg-white/75 px-3 py-3",
                thinkingState === "error" ? "border-red-100/70" : "border-blue-100/60",
              )}
            >
              <div className="space-y-3">
                {parts.map((part, index) => (
                  part.text.trim() ? (
                    <Markdown
                      className="reasoning-markdown-body"
                      key={`${messageId}_reasoning_${index}`}
                    >
                      {part.text}
                    </Markdown>
                  ) : null
                ))}
              </div>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

function groupConsecutiveTools(parts: ToolPart[]): ToolGroup[] {
  const groups: ToolGroup[] = [];
  for (const part of parts) {
    const name = getToolName(part);
    const last = groups.at(-1);
    if (last && last.toolName === name) {
      last.parts.push(part);
    } else {
      groups.push({ toolName: name, parts: [part] });
    }
  }
  return groups;
}

function ToolGroupCard(
  { group, expandAll, isLast }: { group: ToolGroup; expandAll: boolean; isLast: boolean },
) {
  const [expanded, setExpanded] = useState(expandAll);
  const { toolName, parts } = group;
  const count = parts.length;
  const isGroup = count > 1;
  const firstPart = parts[0];
  const status = parts.some((part) => deriveToolStatus(part) === "error")
    ? "error"
    : parts.some((part) => deriveToolStatus(part) === "running")
      ? "running"
      : "success";
  const totalDuration = parts.reduce((sum, part) => {
    if (typeof part.toolMetadata?.duration_ms === "number") {
      return sum + part.toolMetadata.duration_ms;
    }
    return sum;
  }, 0);

  if (!isGroup) {
    return (
      <div className="relative grid grid-cols-[2rem_minmax(0,1fr)] gap-4">
        <div className="relative flex justify-center">
          {!isLast ? (
            <div
              aria-hidden="true"
              className={cn(
                "absolute bottom-[-1rem] top-0 w-px",
                status === "error"
                  ? "bg-red-200"
                  : status === "running"
                    ? "bg-sky-200"
                    : "bg-emerald-200",
              )}
            />
          ) : null}
          <div
            className={cn(
              "relative z-10 mt-1 flex h-8 w-8 items-center justify-center rounded-full border-2 bg-white shadow-[0_6px_12px_rgba(15,23,42,0.08)]",
              status === "error"
                ? "border-red-300 text-red-700"
                : status === "running"
                  ? "border-sky-300 text-sky-700"
                  : "border-emerald-300 text-emerald-700",
            )}
          >
            {status === "error" ? (
              <AlertTriangle className="h-4 w-4" />
            ) : status === "running" ? (
              <span className="dashed-spinner h-4 w-4" />
            ) : (
              <Check className="h-4 w-4" />
            )}
          </div>
        </div>
        <div>
          <ToolCallCard defaultExpanded={expandAll} part={firstPart} />
        </div>
      </div>
    );
  }

  return (
    <div className={cn("relative grid grid-cols-[2rem_minmax(0,1fr)] gap-4", isLast ? "pb-0" : "pb-4")}>
      <div className="relative flex justify-center">
        {!isLast ? (
          <div
            aria-hidden="true"
            className="absolute bottom-[-1rem] top-0 w-px bg-violet-200"
          />
        ) : null}
        <div className="relative z-10 mt-1 flex h-8 w-8 items-center justify-center rounded-full border-2 border-violet-300 bg-white text-violet-700 shadow-[0_6px_12px_rgba(15,23,42,0.08)]">
          <span className="text-[11px] font-bold tabular-nums">{count}</span>
        </div>
      </div>

      <div>
        <div className="overflow-hidden rounded-2xl border border-violet-200/90 bg-violet-50/55 shadow-[0_10px_24px_rgba(76,29,149,0.08)] transition-all duration-200 hover:-translate-y-0.5 hover:shadow-[0_18px_36px_rgba(76,29,149,0.12)]">
          <button
            aria-expanded={expanded}
            className="flex w-full items-start gap-3 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500/30 focus-visible:ring-inset"
            onClick={() => setExpanded((current) => !current)}
            type="button"
          >
            <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-violet-200 bg-violet-100 text-violet-700">
              <span className="text-[11px] font-bold tabular-nums">{count}</span>
            </div>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[14px] font-semibold text-slate-900">
                  {toolName}
                </span>
                <span className="rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                  合并调用
                </span>
                {totalDuration > 0 ? (
                  <span className="font-mono text-[11px] font-semibold tabular-nums text-slate-500">
                    {formatDuration(totalDuration)}
                  </span>
                ) : null}
              </div>
            </div>

            <span
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full text-violet-400 transition-transform duration-200",
                expanded && "rotate-180",
              )}
            >
              <ChevronDown className="h-4 w-4" />
            </span>
          </button>

          <AnimatePresence initial={false}>
            {expanded ? (
              <motion.div
                animate={{ opacity: 1 }}
                className="overflow-hidden"
                exit={{ opacity: 0 }}
                initial={{ opacity: 0 }}
                transition={{ duration: 0.16, ease: "linear" }}
              >
                <motion.div
                  animate={{ opacity: 1, y: 0 }}
                  className="border-t border-violet-200/80 px-4 pb-4 pt-3 will-change-transform"
                  initial={{ opacity: 0.98, y: -3 }}
                  transition={{ duration: 0.28, ease: [0.2, 0.8, 0.2, 1] }}
                >
                  <div className="space-y-3">
                    {parts.map((part) => (
                      <div key={part.toolCallId}>
                        <ToolCallCard defaultExpanded={false} part={part} />
                      </div>
                    ))}
                  </div>
                </motion.div>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

export function MessageBubble({ message, isStreaming = false, expandAllTools = false }: MessageBubbleProps) {
  const [hovered, setHovered] = useState(false);
  const [copied, setCopied] = useState(false);
  const [userCopied, setUserCopied] = useState(false);
  const isUser = message.role === "user";
  const textParts = message.parts.filter(isTextUIPart);
  const toolParts = message.parts.filter(isToolUIPart);
  const toolGroups = useMemo(() => groupConsecutiveTools(toolParts), [toolParts]);
  const reasoningParts = message.parts.filter(isReasoningUIPart);
  const messageText = textParts.map((part) => part.text).join("");

  return (
    <motion.article
      animate={{ opacity: 1, y: 0 }}
      className={cn("group relative flex gap-3", isUser ? "justify-end" : "justify-start")}
      initial={{ opacity: 0, y: 12 }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      transition={{ duration: 0.18, ease: "easeOut" }}
    >
      {!isUser ? (
        <div className="mt-0.5 flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-gray-200 bg-white shadow-sm">
          <Bot className="h-[22px] w-[22px] text-gray-800" />
        </div>
      ) : null}

      <div className={cn("min-w-0 flex-1", isUser && "flex flex-col items-end")}>
        {isUser ? (
          <div className="flex items-start justify-end gap-2">
            <AnimatePresence initial={false}>
              {hovered ? (
                <motion.div
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 4 }}
                  initial={{ opacity: 0, x: 4 }}
                  transition={{ duration: 0.15 }}
                >
                  <IconButton
                    className="h-8 w-8 border border-gray-200 bg-white shadow-sm"
                    label="复制消息"
                    onClick={async () => {
                      try {
                        await navigator.clipboard.writeText(messageText);
                        setUserCopied(true);
                        setTimeout(() => setUserCopied(false), 1500);
                      } catch {
                        // ignore clipboard failure
                      }
                    }}
                  >
                    {userCopied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                  </IconButton>
                </motion.div>
              ) : null}
            </AnimatePresence>
            <div className="ml-auto rounded-2xl rounded-tr-sm border border-gray-200 bg-gray-100 px-4 py-3 text-gray-900 shadow-sm">
              <Markdown className="user-markdown-body">{messageText}</Markdown>
            </div>
          </div>
        ) : (
          <div className="space-y-4">
            <ReasoningBlock
              hasTextOutput={messageText.trim().length > 0}
              messageId={message.id}
              parts={reasoningParts}
              toolParts={toolParts}
            />

            {toolGroups.length > 0 ? (
              <div className="space-y-0">
                {toolGroups.map((group, index) => (
                  <ToolGroupCard
                    expandAll={expandAllTools}
                    group={group}
                    isLast={index === toolGroups.length - 1}
                    key={`${group.toolName}_${group.parts[0]?.toolCallId ?? index}`}
                  />
                ))}
              </div>
            ) : null}

            {textParts.length > 0 ? (
              <Markdown isStreaming={isStreaming}>{messageText}</Markdown>
            ) : null}
          </div>
        )}

        <div className={cn("flex items-center gap-2 font-mono text-xs text-gray-600", isUser && "justify-end")}>
          <span>{message.createdAt ? formatTime(message.createdAt) : ""}</span>
          <AnimatePresence initial={false}>
            {hovered && !isUser ? (
              <motion.div
                animate={{ opacity: 1, y: 0 }}
                className="flex items-center gap-1"
                exit={{ opacity: 0, y: -4 }}
                initial={{ opacity: 0, y: -4 }}
              >
                <IconButton
                  label="复制消息"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(messageText);
                      setCopied(true);
                      setTimeout(() => setCopied(false), 1500);
                    } catch {
                      // ignore clipboard failure
                    }
                  }}
                >
                  {copied ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                </IconButton>
                <IconButton label="重新生成">
                  <RefreshCcw className="h-4 w-4" />
                </IconButton>
                <IconButton label="赞">
                  <ThumbsUp className="h-4 w-4" />
                </IconButton>
                <IconButton label="踩">
                  <ThumbsDown className="h-4 w-4" />
                </IconButton>
              </motion.div>
            ) : null}
          </AnimatePresence>
        </div>
      </div>
    </motion.article>
  );
}
