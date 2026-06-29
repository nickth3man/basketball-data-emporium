import type { ComponentProps } from "react";

import { cn } from "@/lib/cn";

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "icon";

interface ButtonProps extends ComponentProps<"button"> {
  /** Visual color. One of `primary` | `secondary` | `ghost` | `danger`. Defaults to `secondary`. */
  variant?: ButtonVariant;
  /** Density / footprint. `icon` is a square 9x9 button for icon-only use. Defaults to `md`. */
  size?: ButtonSize;
}

/**
 * Variant → Tailwind classes. `primary`/`secondary` are in active use; `ghost`
 * (tertiary) and `danger` (destructive) are provisioned for upcoming actions and
 * have no current call site — kept so the variant set stays complete and stable.
 */
const variants: Record<ButtonVariant, string> = {
  primary: "bg-court-accent text-white hover:bg-teal-800 focus-visible:outline-court-accent",
  secondary: "border border-court-line bg-white text-court-ink hover:bg-zinc-100 focus-visible:outline-court-accent",
  ghost: "text-court-ink hover:bg-zinc-100 focus-visible:outline-court-accent",
  danger: "bg-court-danger text-white hover:bg-red-800 focus-visible:outline-court-danger",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs",
  md: "h-10 px-4 text-sm",
  icon: "size-9 p-0",
};

/**
 * Shared button. `variant` sets color, `size` sets density. Defaults to a
 * secondary, medium-sized, `type="button"` element so it never accidentally
 * submits a surrounding form.
 */
export function Button({ className = "", variant = "secondary", size = "md", type = "button", ...props }: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    />
  );
}
