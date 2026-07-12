/**
 * CommandMenu — the ⌘K power-user surface (cmdk + Radix Dialog).
 *
 * Surfaces:
 *   - Example questions (drop straight into the composer via onPickQuestion)
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
import { Clipboard, Command, Eraser, TrendingUp } from "lucide-react";

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
        "fixed top-[18%] left-1/2 z-50 w-[min(92vw,640px)] -translate-x-1/2",
        "overflow-hidden rounded-2xl border border-border",
        "bg-card shadow-2xl shadow-black/30",
        `
          data-[state=open]:animate-in data-[state=open]:zoom-in-95
          data-[state=open]:fade-in
        `,
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
          "border-b border-border bg-transparent px-4 py-3",
          `font-sans text-sm text-(--color-foreground) placeholder:text-muted-foreground`,
          "focus-visible:outline-none",
        )}
      />
      <CommandList className="max-h-[min(50vh,360px)] overflow-y-auto p-2">
        <CommandEmpty className="py-6 text-center text-sm text-muted-foreground">
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
              <TrendingUp className="mr-2 size-4 shrink-0 text-(--color-primary)" />
              <span className="truncate">{q}</span>
            </CommandItem>
          ))}
        </CommandGroup>

        <CommandSeparator className="my-1 h-px bg-border" />

        <CommandGroup heading="Actions" className={cmdkGroupClass}>
          <CommandItem
            value="copy last answer clipboard"
            onSelect={run(() => onCopyLastAnswer())}
            disabled={!hasHistory}
            className={cmdkItemClass}
          >
            <Clipboard className="mr-2 size-4 shrink-0" />
            Copy last answer
          </CommandItem>
          <CommandItem
            value="clear history session"
            onSelect={run(() => onClearHistory())}
            disabled={!hasHistory}
            className={cmdkItemClass}
          >
            <Eraser className="mr-2 size-4 shrink-0" />
            Clear history
          </CommandItem>
        </CommandGroup>

        <div className="flex items-center justify-between gap-2 px-3 py-2 text-[0.7rem] text-muted-foreground">
          <span className="inline-flex items-center gap-1">
            <Command className="size-3" aria-hidden="true" /> K to toggle
          </span>
          <span>esc to close</span>
        </div>
      </CommandList>
    </CommandDialog>
  );
}

const cmdkItemClass = cn(
  "flex cursor-pointer items-center rounded-lg px-2.5 py-2 text-sm",
  `
    text-(--color-foreground)
    data-[selected=true]:bg-muted
  `,
  "data-[disabled=true]:opacity-40",
);

const cmdkGroupClass = cn(
  "**:[[cmdk-group-heading]]:px-2 **:[[cmdk-group-heading]]:py-1.5",
  `
    **:[[cmdk-group-heading]]:font-display
    **:[[cmdk-group-heading]]:text-[0.65rem]
  `,
  "**:[[cmdk-group-heading]]:font-semibold **:[[cmdk-group-heading]]:uppercase",
  `
    **:[[cmdk-group-heading]]:tracking-wider
    **:[[cmdk-group-heading]]:text-muted-foreground
  `,
);
