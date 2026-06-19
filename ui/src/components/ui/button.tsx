import type { ButtonHTMLAttributes } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg border text-sm font-semibold transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sky-500/25 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary:
          "border-sky-500 bg-sky-500 px-3.5 py-2 text-white hover:bg-sky-600",
        secondary:
          "border-gray-200 bg-white px-3.5 py-2 text-gray-800 hover:bg-gray-50",
        ghost:
          "border-transparent bg-transparent px-2.5 py-2 text-gray-600 hover:bg-sky-50 hover:text-sky-600",
        subtle:
          "border-gray-200 bg-gray-50 px-3 py-2 text-gray-800 hover:bg-sky-50 hover:text-sky-600",
        danger:
          "border-red-200 bg-red-50 px-3.5 py-2 text-red-700 hover:bg-red-100",
      },
      size: {
        default: "h-10",
        sm: "h-8 px-2.5 text-xs",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "secondary",
      size: "default",
    },
  },
);

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants>;

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
