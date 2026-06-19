import { useMemo, useState } from "react";
import ShikiHighlighter from "react-shiki/web";
import { Check, Copy } from "lucide-react";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";
import { cn } from "../lib/utils";

type JsonCodePanelProps = {
  value: string;
  copyLabel?: string;
  maxCollapsedLines?: number;
};

export function JsonCodePanel({ value, copyLabel = "Copy JSON", maxCollapsedLines = 30 }: JsonCodePanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [copied, setCopied] = useState(false);
  const lineCount = useMemo(() => value.split("\n").length, [value]);
  const shouldCollapse = lineCount > maxCollapsedLines;

  function handleCopy() {
    void navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="json-code-panel relative overflow-hidden rounded-lg border border-[#30363d] bg-[#0d1117] shadow-sm">
      <div className="absolute right-2 top-2 z-10 flex items-center gap-2">
        {copied ? (
          <span className="rounded-md border border-emerald-400/20 bg-emerald-400/10 px-2 py-1 font-mono text-[11px] font-semibold text-emerald-200">
            Copied
          </span>
        ) : null}
        <IconButton
          className="h-8 w-8 border border-white/10 bg-white/6 text-slate-200 hover:bg-white/12 hover:text-white"
          label={copyLabel}
          onClick={handleCopy}
        >
          {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
        </IconButton>
      </div>

      <div className={cn("relative overflow-hidden", shouldCollapse && !expanded && "max-h-[34rem]")}>
        <ShikiHighlighter
          addDefaultStyles
          className="json-code-highlighter"
          language="json"
          showLineNumbers
          theme="github-dark"
        >
          {value}
        </ShikiHighlighter>
        {shouldCollapse && !expanded ? (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-24 bg-gradient-to-t from-[#0d1117] to-transparent" />
        ) : null}
      </div>

      {shouldCollapse ? (
        <div className="flex justify-center border-t border-white/8 bg-[#0d1117]/95 px-3 py-2">
          <Button
            className="border-white/10 bg-white/6 font-mono text-[11px] text-slate-200 hover:bg-white/12 hover:text-white"
            onClick={() => setExpanded((current) => !current)}
            size="sm"
            variant="ghost"
          >
            {expanded ? "Collapse" : `Expand all ${lineCount} lines`}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
