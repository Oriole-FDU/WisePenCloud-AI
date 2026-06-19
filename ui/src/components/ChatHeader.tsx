import { Settings2, Sparkles } from "lucide-react";
import type { RuntimeSettings } from "../types/chat";
import { IconButton } from "./ui/icon-button";
import { Panel } from "./ui/panel";

type ChatHeaderProps = {
  title: string;
  settings: RuntimeSettings;
  onOpenSettings: () => void;
};

export function ChatHeader({
  title,
  settings,
  onOpenSettings,
}: ChatHeaderProps) {
  return (
    <Panel className="rounded-xl border-gray-200 px-3 py-2 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 items-center justify-center rounded-lg bg-sky-50 text-sky-500">
              <Sparkles className="h-4 w-4" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate font-display text-base font-semibold text-gray-900">{title}</h1>
              <p className="truncate text-xs font-medium text-gray-600">
                {settings.mode === "mock" ? "演示模式" : "后端流式模式"}
              </p>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <IconButton className="h-10 w-10 border border-gray-200 bg-white shadow-sm" label="打开设置" onClick={onOpenSettings}>
            <Settings2 className="h-4 w-4" />
          </IconButton>
        </div>
      </div>
    </Panel>
  );
}
