import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { ArrowDown } from "lucide-react";
import type { ChatAppMessage } from "../types/chat";
import { MessageBubble } from "./MessageBubble";

type ChatWindowProps = {
  messages: ChatAppMessage[];
  status: "submitted" | "streaming" | "ready" | "error";
};

export function ChatWindow({ messages, status }: ChatWindowProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [autoFollow, setAutoFollow] = useState(true);

  useEffect(() => {
    const element = scrollRef.current;

    if (!element || !autoFollow) {
      return;
    }

    element.scrollTo({
      top: element.scrollHeight,
      behavior: status === "streaming" ? "instant" : "smooth",
    });
  }, [autoFollow, messages, status]);

  function handleScroll() {
    const element = scrollRef.current;

    if (!element) {
      return;
    }

    const distanceFromBottom = element.scrollHeight - element.scrollTop - element.clientHeight;
    setAutoFollow(distanceFromBottom < 80);
  }

  return (
    <section className="relative flex h-full min-h-0 flex-col overflow-hidden rounded-2xl border border-gray-200/45 bg-white/72 shadow-[0_10px_34px_rgba(15,23,42,0.035)] backdrop-blur-sm">
      <div className="scrollbar-thin relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden px-5 py-5 md:px-6" onScroll={handleScroll} ref={scrollRef}>
        {messages.length === 0 ? (
          <div className="flex h-full items-center justify-center">
            <div className="max-w-md text-center">
              <p className="font-display text-2xl font-semibold text-slate-900">开始内部对话</p>
              <p className="mt-2 text-sm font-medium leading-7 text-gray-600">
                当前预览重点展示结构化工具调用、Markdown 回复和可复现的后端调用轨迹。
              </p>
            </div>
          </div>
        ) : (
          <div className="space-y-8 pb-12">
            {messages.map((message, index) => (
              <MessageBubble
                expandAllTools={false}
                isStreaming={status === "streaming" && index === messages.length - 1 && message.role === "assistant"}
                key={message.id}
                message={message}
              />
            ))}
          </div>
        )}

        <AnimatePresence>
          {!autoFollow ? (
            <motion.div
              animate={{ opacity: 1 }}
              className="pointer-events-none sticky bottom-4 flex justify-end pr-2"
              exit={{ opacity: 0 }}
              initial={{ opacity: 0 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
            >
              <button
                aria-label="回到底部"
                className="pointer-events-auto inline-flex h-9 w-9 items-center justify-center rounded-full border border-white/30 bg-white/60 text-gray-700 shadow-md opacity-70 backdrop-blur-md transition-all hover:opacity-100 hover:bg-white/80 hover:shadow-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-gray-400/25"
                onClick={() => {
                  scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
                  setAutoFollow(true);
                }}
                type="button"
              >
                <ArrowDown className="h-4 w-4" />
              </button>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </div>
    </section>
  );
}
