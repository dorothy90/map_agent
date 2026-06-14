import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wide",
  {
    variants: {
      variant: {
        default: "border-transparent bg-primary/15 text-primary",
        muted: "border-border bg-secondary text-muted-foreground",
        good: "border-transparent bg-[var(--good)]/15 text-[var(--good)]",
        warn: "border-transparent bg-[var(--warn)]/15 text-[var(--warn)]",
        bad: "border-transparent bg-[var(--bad)]/15 text-[var(--bad)]",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
