import type { ComponentPropsWithoutRef } from "react";
import { Streamdown } from "streamdown";
import { code } from "@streamdown/code";
import { mermaid } from "@streamdown/mermaid";
import { createMathPlugin } from "@streamdown/math";
import "katex/dist/katex.min.css";
import { cn } from "../../lib/utils";

type MarkdownProps = {
  children: string;
  className?: string;
  isStreaming?: boolean;
};

const math = createMathPlugin({
  singleDollarTextMath: false,
});

const plugins = { code, mermaid, math };

const components = {
  blockquote({ children, ...props }: ComponentPropsWithoutRef<"blockquote">) {
    return (
      <blockquote
        {...props}
        className="my-4 rounded-r-lg border-l-[3px] border-l-sky-600 bg-sky-50/40 p-4 pl-5 text-gray-800 not-italic shadow-sm"
      >
        {children}
      </blockquote>
    );
  },
} as const;

export function Markdown({ children, className, isStreaming }: MarkdownProps) {
  return (
    <Streamdown
      className={cn("markdown-body", className)}
      components={components}
      plugins={plugins}
      isAnimating={isStreaming}
    >
      {children}
    </Streamdown>
  );
}
