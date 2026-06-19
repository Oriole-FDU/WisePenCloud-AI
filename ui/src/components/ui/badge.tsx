import type { HTMLAttributes } from "react";
import { cn } from "../../lib/utils";

export function Badge({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border border-gray-200 bg-white px-2.5 py-1 font-mono text-[11px] font-semibold text-gray-600",
        className,
      )}
      {...props}
    />
  );
}
