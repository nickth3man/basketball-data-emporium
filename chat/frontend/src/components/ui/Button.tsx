/**
 * Tiny shared button (PLAN §8.3, custom UI shell — no shadcn CLI).
 *
 * Variants:
 *   - "primary"  → filled NBA-orange (uses --color-primary, tuned for AA)
 *   - "subtle"   → bordered neutral (default for in-bubble actions)
 *   - "ghost"    → text-only (for cancel / dismiss affordances)
 *   - "danger"   → red-tinted (for destructive confirm actions)
 *
 * Forwards `className` (merged via `cn`) so callers can compose layout
 * classes; renders an accessible `<button type="button">` with a visible
 * focus ring. Defers to native disabled semantics (browsers don't fire
 * click on a disabled button — no extra guard needed).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "subtle" | "ghost" | "danger";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  /** Icon-only sizing: tighter padding for square icon buttons. */
  size?: "sm" | "md" | "icon";
  children: ReactNode;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)] " +
    "shadow-sm shadow-[color:var(--color-primary)]/20 " +
    "hover:brightness-110 active:brightness-95 disabled:opacity-50",
  subtle:
    "border border-[color:var(--color-border)] bg-[color:var(--color-card)] " +
    "text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)] " +
    "disabled:opacity-50",
  ghost:
    "bg-transparent text-[color:var(--color-muted-foreground)] " +
    "hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)] " +
    "disabled:opacity-40",
  danger:
    "bg-[color:var(--color-danger-fg)] text-white shadow-sm " +
    "hover:brightness-110 active:brightness-95 disabled:opacity-50",
};

const SIZE_CLASSES: Record<NonNullable<ButtonProps["size"]>, string> = {
  sm: "h-7 px-2.5 text-xs gap-1",
  md: "h-9 px-3.5 text-sm gap-1.5",
  icon: "h-8 w-8 p-0",
};

const BASE_CLASSES =
  "inline-flex select-none items-center justify-center rounded-lg font-medium " +
  "transition-[filter,background-color,color,border-color] duration-150 " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)] " +
  "disabled:cursor-not-allowed";

export function Button({
  variant = "primary",
  size = "md",
  className,
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cn(BASE_CLASSES, VARIANT_CLASSES[variant], SIZE_CLASSES[size], className)}
      {...rest}
    >
      {children}
    </button>
  );
}
