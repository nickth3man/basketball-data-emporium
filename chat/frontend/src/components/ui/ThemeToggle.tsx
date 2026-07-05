/**
 * ThemeToggle — cycles light → dark → system using next-themes.
 *
 * Renders a single icon-button. To avoid a hydration/flash mismatch the
 * icon is gated on `mounted` (next-themes resolves the theme on the
 * client); until then we render a stable placeholder so the button keeps
 * its layout and stays focusable.
 */
import { Moon, Sun, Monitor } from "lucide-react";
import { useEffect, useState } from "react";
import { useTheme } from "next-themes";

import { cn } from "@/lib/utils";

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, theme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Cycle: light → dark → system. `theme` is what we set; `resolvedTheme`
  // is what's actually rendering (system → light|dark).
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const label = mounted
    ? `Switch theme (current: ${resolvedTheme ?? theme}). Next: ${next}`
    : "Switch theme";

  const Icon = !mounted ? Monitor : resolvedTheme === "dark" ? Moon : Sun;

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={label}
      title="Toggle theme"
      className={cn(
        "inline-flex h-8 w-8 items-center justify-center rounded-lg text-[color:var(--color-muted-foreground)]",
        "transition-colors hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)]",
        className,
      )}
    >
      <Icon className="h-[1.05rem] w-[1.05rem]" aria-hidden="true" />
    </button>
  );
}
