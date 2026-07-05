/**
 * CommandMenu — the ⌘K power-user surface (cmdk + Radix Dialog).
 *
 * Surfaces:
 *   - Example questions (drop straight into the composer via onPickQuestion)
 *   - Theme cycling (light / dark / system) via next-themes
 *   - "Copy last answer" (ChatView owns the last assistant text)
 *   - "Clear history" (ChatView owns the session id)
 *
 * A single global keydown listener opens the palette on ⌘K / Ctrl+K (and
 * closes on Escape, which Radix Dialog handles natively). The listener is
 * gated to avoid swallowing the key when the user is composing IME input
 * or focused on a form field with a modifier-less shortcut — ⌘/Ctrl is a
 * safe, conventional combination.
 */
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "cmdk";
import { useCallback, useEffect, useState } from "react";
import { Clipboard, Command, Eraser, Moon, Monitor, Sun, TrendingUp } from "lucide-react";
import { useTheme } from "next-themes";

import { cn } from "@/lib/utils";

export interface CommandMenuProps {
  examples: string[];
  /** Fire a question into the composer/send pipeline. */
  onPickQuestion: (q: string) => void;
  /** Copy the last assistant answer; returns whether anything was copied. */
  onCopyLastAnswer: () => boolean;
  /** Clear the active session's visible history. */
  onClearHistory: () => void;
  /** Whether there is any history to act on (gates the history items). */
  hasHistory: boolean;
  /** Controlled open state. When omitted, the menu self-manages. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

export function CommandMenu({
  examples,
  onPickQuestion,
  onCopyLastAnswer,
  onClearHistory,
  hasHistory,
  open: openProp,
  onOpenChange,
}: CommandMenuProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = openProp ?? internalOpen;
  const setOpen = useCallback(
    (v: boolean) => {
      if (onOpenChange) onOpenChange(v);
      setInternalOpen(v);
    },
    [onOpenChange],
  );
  const { theme, setTheme } = useTheme();

  // Global ⌘K / Ctrl+K toggles the palette (works whether the parent
  // controls the state or not — both paths route through `setOpen`).
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen(!open);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, setOpen]);

  const run = (fn: () => void) => () => {
    fn();
    setOpen(false);
  };

  return (
    <CommandDialog
      open={open}
      onOpenChange={setOpen}
      label="Command menu"
      overlayClassName="fixed inset-0 z-50 bg-black/50 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in"
      contentClassName={cn(
        "fixed left-1/2 top-[18%] z-50 w-[min(92vw,640px)] -translate-x-1/2",
        "overflow-hidden rounded-2xl border border-[color:var(--color-border)]",
        "bg-[color:var(--color-card)] shadow-2xl shadow-black/30",
        "data-[state=open]:animate-in data-[state=open]:fade-in data-[state=open]:zoom-in-95",
      )}
      // CommandDialog renders its own <Command> root; these props flow
      // into it, so the Input/List below share its state. Do NOT wrap
      // them in a second <Command> — that nests roots and breaks the
      // roving-focus store.
      className="flex flex-col"
      filter={(value, search) => (value.toLowerCase().includes(search.toLowerCase()) ? 1 : 0)}
    >
      <CommandInput
        placeholder="Type a command or search questions…"
        className={cn(
          "border-b border-[color:var(--color-border)] bg-transparent px-4 py-3",
          "font-sans text-sm text-[color:var(--color-foreground)] placeholder:text-[color:var(--color-muted-foreground)]",
          "focus-visible:outline-none",
        )}
      />
      <CommandList className="max-h-[min(50vh,360px)] overflow-y-auto p-2">
        <CommandEmpty className="py-6 text-center text-sm text-[color:var(--color-muted-foreground)]">
          No results.
        </CommandEmpty>

        <CommandGroup heading="Example questions" className={cmdkGroupClass}>
          {examples.map((q) => (
            <CommandItem
              key={q}
              value={`example ${q}`}
              onSelect={run(() => onPickQuestion(q))}
              className={cmdkItemClass}
            >
              <TrendingUp className="mr-2 h-4 w-4 shrink-0 text-[color:var(--color-primary)]" />
              <span className="truncate">{q}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator className="my-1 h-px bg-[color:var(--color-border)]" />

        <CommandGroup heading="Appearance" className={cmdkGroupClass}>
          <CommandItem
            value="theme light"
            onSelect={run(() => setTheme("light"))}
            className={cmdkItemClass}
          >
            <Sun className="mr-2 h-4 w-4 shrink-0" />
            Light theme
            {theme === "light" && <ActiveDot />}
          </CommandItem>
          <CommandItem
            value="theme dark"
            onSelect={run(() => setTheme("dark"))}
            className={cmdkItemClass}
          >
            <Moon className="mr-2 h-4 w-4 shrink-0" />
            Dark theme
            {theme === "dark" && <ActiveDot />}
          </CommandItem>
          <CommandItem
            value="theme system auto"
            onSelect={run(() => setTheme("system"))}
            className={cmdkItemClass}
          >
            <Monitor className="mr-2 h-4 w-4 shrink-0" />
            System theme
            {theme === "system" && <ActiveDot />}
          </CommandItem>
        </CommandGroup>

        <CommandSeparator className="my-1 h-px bg-[color:var(--color-border)]" />

        <CommandGroup heading="Actions" className={cmdkGroupClass}>
          <CommandItem
            value="copy last answer clipboard"
            onSelect={run(() => onCopyLastAnswer())}
            disabled={!hasHistory}
            className={cmdkItemClass}
          >
            <Clipboard className="mr-2 h-4 w-4 shrink-0" />
            Copy last answer
          </CommandItem>
          <CommandItem
            value="clear history session"
            onSelect={run(() => onClearHistory())}
            disabled={!hasHistory}
            className={cmdkItemClass}
          >
            <Eraser className="mr-2 h-4 w-4 shrink-0" />
            Clear history
          </CommandItem>
        </CommandGroup>

        <div className="flex items-center justify-between gap-2 px-3 py-2 text-[0.7rem] text-[color:var(--color-muted-foreground)]">
          <span className="inline-flex items-center gap-1">
            <Command className="h-3 w-3" aria-hidden="true" /> K to toggle
          </span>
          <span>esc to close</span>
        </div>
      </CommandList>
    </CommandDialog>
  );
}

const cmdkItemClass = cn(
  "flex cursor-pointer items-center rounded-lg px-2.5 py-2 text-sm",
  "text-[color:var(--color-foreground)] data-[selected=true]:bg-[color:var(--color-muted)]",
  "data-[disabled=true]:opacity-40",
);

const cmdkGroupClass = cn(
  "[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5",
  "[&_[cmdk-group-heading]]:font-display [&_[cmdk-group-heading]]:text-[0.65rem]",
  "[&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:uppercase",
  "[&_[cmdk-group-heading]]:tracking-wider [&_[cmdk-group-heading]]:text-[color:var(--color-muted-foreground)]",
);

function ActiveDot() {
  return (
    <span
      aria-hidden="true"
      className="ml-auto h-1.5 w-1.5 rounded-full bg-[color:var(--color-primary)]"
    />
  );
}
