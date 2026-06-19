import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { AnimatePresence, LayoutGroup, motion } from "framer-motion";
import { Menu, MessageSquarePlus, MoreHorizontal, Pencil, Pin, PinOff, Search, Settings2, Trash2, Trash2Icon } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import type { SessionSummary } from "../types/chat";
import { cn, formatTime } from "../lib/utils";
import { Button } from "./ui/button";
import { IconButton } from "./ui/icon-button";
import { Panel } from "./ui/panel";

type SidebarProps = {
  sessions: SessionSummary[];
  activeSessionId: string;
  onSelectSession: (sessionId: string) => void;
  onCreateSession: () => void;
  onDeleteSession: (sessionId: string) => void;
  onDeleteAllSessions: () => void;
  onRenameSession: (sessionId: string, newTitle: string) => void;
  onPinSession: (sessionId: string, setPin: boolean) => void;
  onOpenSettings: () => void;
  mobileOpen: boolean;
  onMobileOpenChange: (open: boolean) => void;
};

function SidebarBody({
  activeSessionId,
  onCreateSession,
  onDeleteSession,
  onDeleteAllSessions,
  onRenameSession,
  onPinSession,
  onOpenSettings,
  onSelectSession,
  sessions,
}: Omit<SidebarProps, "mobileOpen" | "onMobileOpenChange">) {
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  const startRename = useCallback((session: SessionSummary) => {
    setRenamingId(session.id);
    setRenameValue(session.title);
    setTimeout(() => renameInputRef.current?.select(), 0);
  }, []);

  const commitRename = useCallback(() => {
    if (renamingId && renameValue.trim()) {
      onRenameSession(renamingId, renameValue.trim());
    }
    setRenamingId(null);
  }, [renamingId, renameValue, onRenameSession]);

  const pinnedSessions = sessions.filter((s) => s.pinned);
  const unpinnedSessions = sessions.filter((s) => !s.pinned);

  function renderSessionItem(session: SessionSummary) {
    const active = session.id === activeSessionId;
    const isRenaming = renamingId === session.id;

    return (
      <motion.button
        animate={{ opacity: 1, y: 0 }}
        className={cn(
          "group relative w-full overflow-hidden rounded-lg border px-3 py-2.5 text-left transition-colors duration-150",
          active
            ? "border-transparent"
            : "border-transparent bg-transparent hover:border-gray-200 hover:bg-gray-50",
        )}
        exit={{ opacity: 0, y: -8 }}
        initial={{ opacity: 0, y: -8 }}
        key={session.id}
        layout
        onClick={() => !isRenaming && onSelectSession(session.id)}
        transition={{ type: "spring", stiffness: 500, damping: 35 }}
        type="button"
        whileHover={active ? undefined : { backgroundColor: "#f9fafb" }}
        whileTap={isRenaming ? undefined : { scale: 0.98 }}
      >
        {active ? (
          <motion.div
            className="absolute inset-0 rounded-lg border-l-2 border-sky-400 bg-sky-50/60"
            layoutId="sidebar-active-indicator"
            transition={{ type: "spring", stiffness: 500, damping: 35 }}
          />
        ) : null}
        <div className="relative z-10 flex items-center justify-between gap-2">
          <div className="min-w-0 flex-1">
            {isRenaming ? (
              <input
                className="w-full rounded border border-gray-300 bg-white px-1.5 py-0.5 text-sm font-semibold text-gray-900 outline-none focus:border-sky-400"
                onBlur={commitRename}
                onChange={(e) => setRenameValue(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") commitRename();
                  if (e.key === "Escape") setRenamingId(null);
                }}
                ref={renameInputRef}
                value={renameValue}
              />
            ) : (
              <p className="truncate text-sm font-semibold text-gray-900">{session.title}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {session.pinned && <Pin className="h-3 w-3 text-gray-400" />}
            <span className="font-mono text-[11px] text-gray-500">{formatTime(session.updatedAt)}</span>
            <DropdownMenu.Root>
              <DropdownMenu.Trigger asChild>
                <span
                  className="ml-0.5 inline-flex h-5 w-5 items-center justify-center rounded opacity-0 transition-opacity hover:bg-gray-200 group-hover:opacity-100"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreHorizontal className="h-3.5 w-3.5 text-gray-500" />
                </span>
              </DropdownMenu.Trigger>
              <DropdownMenu.Portal>
                <DropdownMenu.Content
                  align="end"
                  className="z-50 min-w-[140px] rounded-lg border border-gray-200 bg-white p-1 shadow-lg"
                  sideOffset={4}
                >
                  <DropdownMenu.Item
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-gray-700 outline-none data-[highlighted]:bg-sky-50"
                    onClick={(e) => { e.stopPropagation(); startRename(session); }}
                  >
                    <Pencil className="h-3.5 w-3.5" /> 重命名
                  </DropdownMenu.Item>
                  <DropdownMenu.Item
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-gray-700 outline-none data-[highlighted]:bg-sky-50"
                    onClick={(e) => { e.stopPropagation(); onPinSession(session.id, !session.pinned); }}
                  >
                    {session.pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                    {session.pinned ? "取消置顶" : "置顶"}
                  </DropdownMenu.Item>
                  <DropdownMenu.Separator className="my-1 h-px bg-gray-100" />
                  <DropdownMenu.Item
                    className="flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-red-600 outline-none data-[highlighted]:bg-red-50"
                    onClick={(e) => { e.stopPropagation(); onDeleteSession(session.id); }}
                  >
                    <Trash2 className="h-3.5 w-3.5" /> 删除
                  </DropdownMenu.Item>
                </DropdownMenu.Content>
              </DropdownMenu.Portal>
            </DropdownMenu.Root>
          </div>
        </div>
        {!isRenaming && session.preview && (
          <p className="relative z-10 mt-0.5 truncate text-xs font-medium text-gray-500">{session.preview}</p>
        )}
      </motion.button>
    );
  }
  return (
    <Panel className="flex h-full flex-col overflow-hidden border-gray-200 shadow-sm">
      <div className="border-b border-gray-200 p-4">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="font-display text-lg font-semibold text-gray-900">WisePen Chat</p>
            <p className="text-sm font-medium text-gray-600">内部预览控制台</p>
          </div>
        </div>
        <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
          <Button
            className="w-full justify-center border-sky-500 bg-white text-sky-600 hover:bg-sky-50"
            onClick={onCreateSession}
            variant="secondary"
          >
            <MessageSquarePlus className="h-4 w-4" />
            新建对话
          </Button>
        </motion.div>
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
          <Search className="h-4 w-4 text-gray-600" />
          <input
            aria-label="搜索会话"
            className="w-full border-0 bg-transparent text-sm font-medium text-gray-900 outline-none placeholder:text-gray-500"
            placeholder="搜索"
          />
        </div>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto p-3">
        <LayoutGroup>
          <div className="space-y-3">
            {pinnedSessions.length > 0 && (
              <div>
                <p className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">置顶</p>
                <div className="space-y-1">
                  <AnimatePresence initial={false}>
                    {pinnedSessions.map((session) => renderSessionItem(session))}
                  </AnimatePresence>
                </div>
              </div>
            )}
            {unpinnedSessions.length > 0 && (
              <div>
                {pinnedSessions.length > 0 && (
                  <p className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wider text-gray-400">最近</p>
                )}
                <div className="space-y-1">
                  <AnimatePresence initial={false}>
                    {unpinnedSessions.map((session) => renderSessionItem(session))}
                  </AnimatePresence>
                </div>
              </div>
            )}
          </div>
        </LayoutGroup>
      </div>

      <div className="border-t border-gray-200 p-3">
        <div className="space-y-2">
          <Button
            className="w-full justify-start bg-white text-gray-700 hover:bg-sky-50 hover:text-sky-600"
            onClick={onOpenSettings}
            type="button"
            variant="secondary"
          >
            <Settings2 className="h-4 w-4" />
            运行设置
          </Button>
          {sessions.length > 0 && (
            <Button
              className="w-full justify-start bg-white text-gray-400 hover:bg-red-50 hover:text-red-500"
              onClick={onDeleteAllSessions}
              type="button"
              variant="ghost"
            >
              <Trash2Icon className="h-4 w-4" />
              清除全部会话
            </Button>
          )}
        </div>
      </div>
    </Panel>
  );
}

export function Sidebar(props: SidebarProps) {
  return (
    <>
      <div className="hidden w-[312px] shrink-0 xl:block">
        <SidebarBody {...props} />
      </div>

      <div className="xl:hidden">
        <Dialog.Root onOpenChange={props.onMobileOpenChange} open={props.mobileOpen}>
          <Dialog.Trigger asChild>
            <IconButton className="h-10 w-10 border border-gray-200 bg-white shadow-sm" label="打开侧栏">
              <Menu className="h-4 w-4" />
            </IconButton>
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/18 backdrop-blur-sm" />
            <Dialog.Content className="fixed inset-y-0 left-0 z-50 w-[min(92vw,360px)] p-3 outline-none">
              <SidebarBody {...props} />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      </div>
    </>
  );
}
