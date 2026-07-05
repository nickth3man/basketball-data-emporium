/**
 * Tiny shared button (PLAN §8.3, custom UI shell — no shadcn CLI).
 *
 * Variants:
 *   - "primary"  → filled (uses the `--color-primary` token from globals.css)
 *   - "subtle"   → bordered neutral (default for in-bubble actions)
 *   - "ghost"    → text-only (for cancel / dismiss affordances)
 *
 * Forwards `className` so callers can compose layout classes; renders an
 * accessible `<button type="button">` with a visible focus ring via
 * `focus-visible:` Tailwind utilities. Defers to native disabled semantics
 * (browsers don't fire click on a disabled button — no extra guard needed).
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

export type ButtonVariant = "primary" | "subtle" | "ghost";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  children: ReactNode;
}

const VARIANT_CLASSES: Record<ButtonVariant, string> = {
  primary:
    "bg-[color:var(--color-primary)] text-[color:var(--color-primary-foreground)] hover:opacity-90 disabled:opacity-50",
  subtle:
    "border border-[color:var(--color-border)] bg-[color:var(--color-background)] text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)] disabled:opacity-50",
  ghost:
    "bg-transparent text-[color:var(--color-foreground)] hover:bg-[color:var(--color-muted)] disabled:opacity-40",
};

const BASE_CLASSES =
  "inline-flex items-center justify-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium " +
  "transition-colors " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] " +
  "focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)] " +
  "disabled:cursor-not-allowed";

export function Button({
  variant = "primary",
  className = "",
  type = "button",
  children,
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={`${BASE_CLASSES} ${VARIANT_CLASSES[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}