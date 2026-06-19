import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Panel({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-[var(--app-border)] bg-[var(--app-panel)] shadow-[var(--app-shadow-soft)] backdrop-blur-sm",
        className,
      )}
      {...props}
    />
  );
}
