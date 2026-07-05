/**
 * ChatView (PLAN §8.3, §13, §15 Phase 7 error-UX exit) — v2 premium shell.
 *
 * The composition root for the chat tab. Owns:
 *   - The active session (lifecycle via `useSessions`).
 *   - The message timeline (history loaded from `getSessionHistory`,
 *     in-flight assistant turns appended as `useChatTurn` settles).
 *   - The composer (textarea + Send, Enter to send / Shift+Enter newline).
 *   - The Cancel affordance when a turn runs > 5 s (§13).
 *   - The elapsed timer (visible while running > 1 s).
 *   - The error banner with a retry affordance (motion enter/exit).
 *   - A muted inline "Cancelled." note (role=status) when the user
 *     cancels.
 *   - The clarification prompt (when `state.clarification` is set).
 *
 * The SSE plumbing lives in `useChatTurn`; this component is the wiring
 * contract per the Phase 5+7 spec.
 *
 * v2 additions: a ⌘K command palette, a theme toggle, sonner toasts on
 * copy/clear, and lucide icons throughout — layered WITHOUT touching the
 * SSE transport or the hook contracts.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  AlertTriangle,
  Ban,
  Command as CommandIcon,
  Loader2,
  RotateCw,
  Send,
  Square,
  Timer,
} from "lucide-react";
import { toast } from "sonner";

import { ChatTimeline, type TimelineMessage } from "@/components/ChatTimeline";
import { ClarifyPrompt } from "@/components/ClarifyPrompt";
import { ClearHistoryButton } from "@/components/ClearHistoryButton";
import { CommandMenu } from "@/components/CommandMenu";
import { Button } from "@/components/ui/Button";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { deleteSession, getSessionHistory, type SessionMessage } from "@/api/client";
import { useChatTurn, type ChatTurnError, type ChatTurnState } from "@/hooks/useChatTurn";
import { useSessions, type HealthStatus } from "@/hooks/useSessions";
import { cn } from "@/lib/utils";

/** Show the cancel button after this many seconds of a running turn (§13). */
const CANCEL_AFTER_MS = 5_000;
/** Show the elapsed timer after this many seconds (§13). */
const TIMER_AFTER_MS = 1_000;

const EXAMPLE_QUESTIONS: string[] = [
  "Who shot 50/40/90 with at least 25 PPG?",
  "Show me the largest scoring run in a Finals game since 2010.",
  "Most career assists in games where the player scored 0.",
];

function historyToTimeline(messages: SessionMessage[]): TimelineMessage[] {
  return messages.map((m) => ({
    role: m.role === "assistant" ? "assistant" : "user",
    content: m.content,
  }));
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return `${m}m ${rs}s`;
}

function errorBannerText(err: ChatTurnError): { title: string; detail: string } {
  switch (err.code) {
    case "network":
      return {
        title: "Connection lost. Check the server is running.",
        detail: "We couldn’t reach the chat backend. Once it’s back, hit Retry.",
      };
    case "query_timeout":
      return {
        title: "The query took too long and was cancelled (300s limit).",
        detail: "Try a more specific question, or break this into a smaller piece.",
      };
    default:
      return {
        title: "Something went wrong.",
        detail: err.message,
      };
  }
}

export function ChatView() {
  const { sessions, create, health } = useSessions();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<TimelineMessage[]>([]);
  const [composer, setComposer] = useState<string>("");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);
  const [now, setNow] = useState<number>(() => Date.now());
  const [lastError, setLastError] = useState<ChatTurnError | null>(null);
  const [lastCancelled, setLastCancelled] = useState<boolean>(false);
  const [paletteOpen, setPaletteOpen] = useState<boolean>(false);
  const turnStartedAtRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (sessionId !== null) return;
    if (sessions.length > 0 && sessions[0]) {
      setSessionId(sessions[0].id);
    }
  }, [sessions, sessionId]);

  useEffect(() => {
    if (sessionId === null) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError(null);
    getSessionHistory(sessionId)
      .then((page) => {
        if (cancelled) return;
        setMessages(historyToTimeline(page.messages));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setHistoryError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  const { state, send, cancel, reset } = useChatTurn(sessionId);

  useEffect(() => {
    if (state.status !== "running") return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [state.status]);

  const settleAnswer = useCallback(
    (s: ChatTurnState) => {
      if (s.status === "cancelled") {
        reset();
        return;
      }
      const answer =
        s.answer.length > 0
          ? s.answer
          : s.status === "error"
            ? "(error: no answer)"
            : "(no answer)";
      setMessages((prev) => {
        const last = prev[prev.length - 1];
        if (last && last.role === "assistant" && last.content === answer) return prev;
        return [...prev, { role: "assistant", content: answer, turn: s }];
      });
      reset();
    },
    [reset],
  );

  useEffect(() => {
    if (state.status !== "done" && state.status !== "error" && state.status !== "cancelled") {
      return;
    }
    if (state.status === "error" && state.error !== null) {
      setLastError(state.error);
      setLastCancelled(false);
    } else if (state.status === "cancelled") {
      setLastCancelled(true);
      setLastError(null);
    } else {
      setLastError(null);
      setLastCancelled(false);
    }
    settleAnswer(state);
  }, [state, settleAnswer]);

  const handleSubmit = useCallback(
    async (text: string): Promise<void> => {
      const trimmed = text.trim();
      if (trimmed.length === 0) return;

      let sid = sessionId;
      if (sid === null) {
        const created = await create(null);
        sid = created.id;
        setSessionId(sid);
      }

      setLastError(null);
      setLastCancelled(false);

      setMessages((prev) => [...prev, { role: "user", content: trimmed }]);
      setComposer("");
      await send(trimmed);
    },
    [sessionId, create, send],
  );

  const handleFormSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      void handleSubmit(composer);
    },
    [composer, handleSubmit],
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      // Enter to send, Shift+Enter for newline (§8.4). Don't hijack IME
      // composition (isComposing) or the modifier-key variants.
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        void handleSubmit(composer);
      }
    },
    [composer, handleSubmit],
  );

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    setComposer(e.target.value);
  }, []);

  const handleCancel = useCallback(() => {
    cancel();
  }, [cancel]);

  const handleCleared = useCallback(() => {
    setMessages([]);
    setLastError(null);
    setLastCancelled(false);
  }, []);

  const isRunning = state.status === "running";

  useEffect(() => {
    if (state.status === "running") {
      if (turnStartedAtRef.current === null) {
        turnStartedAtRef.current = Date.now();
      }
    } else {
      turnStartedAtRef.current = null;
    }
  }, [state.status]);
  const turnElapsed =
    isRunning && turnStartedAtRef.current !== null ? now - turnStartedAtRef.current : 0;
  const showTimer = isRunning && turnElapsed >= TIMER_AFTER_MS;
  const showCancel = isRunning && turnElapsed >= CANCEL_AFTER_MS;

  const retryText = useMemo(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    return lastUser?.content ?? "";
  }, [messages]);

  const handleRetry = useCallback(() => {
    if (retryText.length === 0) return;
    void handleSubmit(retryText);
  }, [handleSubmit, retryText]);

  const retryAriaLabel = useMemo(() => {
    const snippet = retryText.replace(/\s+/g, " ").trim().slice(0, 40);
    return snippet.length > 0 ? `Retry: ${snippet}` : "Retry";
  }, [retryText]);

  const showClarify =
    state.status === "running" && state.clarification !== null && state.clarification !== undefined;
  const showErrorBanner = lastError !== null && !isRunning;
  const showCancelledNote = lastCancelled && lastError === null && !isRunning;
  const bannerText = lastError ? errorBannerText(lastError) : null;

  // Command palette wiring.
  const handleCopyLastAnswer = useCallback((): boolean => {
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (!lastAssistant || lastAssistant.content.length === 0) {
      toast.error("No answer to copy yet");
      return false;
    }
    void navigator.clipboard
      .writeText(lastAssistant.content)
      .then(() => toast.success("Answer copied"))
      .catch(() => toast.error("Couldn't copy — clipboard unavailable"));
    return true;
  }, [messages]);

  const handleClearFromPalette = useCallback(() => {
    if (sessionId === null) return;
    if (
      !window.confirm(
        "Clear this session's visible chat history? The underlying debug logs are kept.",
      )
    ) {
      return;
    }
    void (async () => {
      try {
        await deleteSession(sessionId);
        handleCleared();
        toast.success("History cleared");
      } catch {
        toast.error("Couldn't clear history");
      }
    })();
  }, [sessionId, handleCleared]);

  const hasHistory = messages.length > 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <Header
        health={health}
        sessionId={sessionId}
        onCleared={handleCleared}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <ChatTimeline
        messages={messages}
        liveTurn={isRunning ? state : null}
        examples={EXAMPLE_QUESTIONS}
        onPickExample={(q) => {
          setComposer(q);
          // Focus the composer so the user can edit before sending, or
          // hit Enter to fire immediately.
          textareaRef.current?.focus();
        }}
        status={
          isRunning
            ? "running"
            : showErrorBanner
              ? "error"
              : showCancelledNote
                ? "cancelled"
                : state.status === "done"
                  ? "done"
                  : "idle"
        }
      />

      {showClarify && state.clarification && (
        <div className="border-t border-[color:var(--color-border)] bg-[color:var(--color-muted)]/40 px-4 py-3 sm:px-6">
          <ClarifyPrompt
            question={state.clarification.question}
            options={state.clarification.options ?? null}
            disabled={isRunning}
            onAnswer={(text) => {
              void handleSubmit(text);
            }}
          />
        </div>
      )}

      <AnimateBanner show={showErrorBanner && lastError !== null && bannerText !== null}>
        {lastError && bannerText && (
          <div
            role="alert"
            aria-live="assertive"
            className={cn(
              "flex items-center justify-between gap-3 border-t px-4 py-3 sm:px-6",
              "border-[color:var(--color-danger-border)] bg-[color:var(--color-danger-bg)]",
            )}
          >
            <div className="flex items-start gap-2.5">
              <AlertTriangle
                className="mt-0.5 h-4 w-4 shrink-0 text-[color:var(--color-danger-fg)]"
                aria-hidden="true"
              />
              <div className="flex flex-col gap-0.5">
                <p className="font-medium text-[color:var(--color-danger-fg)]">
                  {bannerText.title}
                </p>
                {bannerText.detail.length > 0 && (
                  <p className="text-xs opacity-80 text-[color:var(--color-danger-fg)]">
                    {bannerText.detail}
                  </p>
                )}
                <p className="text-[0.7rem] opacity-60 text-[color:var(--color-danger-fg)]">
                  Code: {lastError.code}
                </p>
              </div>
            </div>
            <Button
              type="button"
              variant="subtle"
              size="sm"
              disabled={retryText.length === 0}
              onClick={handleRetry}
              aria-label={retryAriaLabel}
            >
              <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
              Retry
            </Button>
          </div>
        )}
      </AnimateBanner>

      <AnimateBanner show={showCancelledNote}>
        <div
          role="status"
          aria-live="polite"
          className={cn(
            "flex items-center justify-between gap-3 border-t px-4 py-2.5 sm:px-6",
            "border-[color:var(--color-border)] bg-[color:var(--color-muted)]",
            "text-sm text-[color:var(--color-muted-foreground)]",
          )}
        >
          <span className="inline-flex items-center gap-2">
            <Ban className="h-3.5 w-3.5" aria-hidden="true" />
            Cancelled.
          </span>
          <Button
            type="button"
            variant="subtle"
            size="sm"
            disabled={retryText.length === 0}
            onClick={handleRetry}
            aria-label={retryAriaLabel}
          >
            <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
            Retry
          </Button>
        </div>
      </AnimateBanner>

      <Composer
        composer={composer}
        isRunning={isRunning}
        disabled={isRunning}
        textareaRef={textareaRef}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onSubmit={handleFormSubmit}
        onCancel={handleCancel}
        showCancel={showCancel}
        showTimer={showTimer}
        turnElapsed={turnElapsed}
        historyLoading={historyLoading}
        historyError={historyError}
      />

      <CommandMenu
        examples={EXAMPLE_QUESTIONS}
        onPickQuestion={(q) => {
          setComposer(q);
          textareaRef.current?.focus();
        }}
        onCopyLastAnswer={handleCopyLastAnswer}
        onClearHistory={handleClearFromPalette}
        hasHistory={hasHistory}
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

interface HeaderProps {
  health: HealthStatus;
  sessionId: string | null;
  onCleared: () => void;
  onOpenPalette: () => void;
}

function Header({ health, sessionId, onCleared, onOpenPalette }: HeaderProps) {
  return (
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--color-border)] bg-[color:var(--color-card)]/60 px-4 py-3 backdrop-blur-sm sm:px-6">
      <div className="flex items-center gap-3">
        <div
          aria-hidden="true"
          className="flex h-9 w-9 items-center justify-center rounded-xl bg-[color:var(--color-primary)]/12 text-[color:var(--color-primary)] ring-1 ring-inset ring-[color:var(--color-primary)]/25"
        >
          {/* A simple basketball-ish glyph: a circle with seams. */}
          <svg
            viewBox="0 0 24 24"
            className="h-5 w-5"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M3 12h18M12 3v18M5.6 5.6c4 3 4 9.8 0 12.8M18.4 5.6c-4 3-4 9.8 0 12.8" />
          </svg>
        </div>
        <div className="flex flex-col">
          <h1 className="font-display text-base font-semibold leading-tight tracking-tight">
            Basketball Data Chatbot
          </h1>
          <p className="text-xs text-[color:var(--color-muted-foreground)]">
            Answers grounded in the warehouse — never from model memory.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-1.5">
        <HealthBadge status={health} />
        <button
          type="button"
          onClick={onOpenPalette}
          aria-label="Open command palette"
          title="Command palette (⌘K)"
          className={cn(
            "inline-flex h-8 items-center gap-1 rounded-lg border border-[color:var(--color-border)]",
            "bg-[color:var(--color-card)] px-2 text-xs text-[color:var(--color-muted-foreground)]",
            "transition-colors hover:bg-[color:var(--color-muted)] hover:text-[color:var(--color-foreground)]",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-card)]",
          )}
        >
          <CommandIcon className="h-3.5 w-3.5" aria-hidden="true" />
          <kbd className="font-sans text-[0.7rem]">⌘K</kbd>
        </button>
        <ThemeToggle />
        {sessionId !== null && <ClearHistoryButton sessionId={sessionId} onCleared={onCleared} />}
      </div>
    </header>
  );
}

function HealthBadge({ status }: { status: HealthStatus }) {
  const tone =
    status === "ok"
      ? "border-[color:var(--color-ok-fg)]/30 bg-[color:var(--color-ok-fg)]/10 text-[color:var(--color-ok-fg)]"
      : status === "degraded"
        ? "border-[color:var(--color-warn-border)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn-fg)]"
        : "border-[color:var(--color-border)] bg-[color:var(--color-muted)] text-[color:var(--color-muted-foreground)]";
  const label = status === "ok" ? "connected" : status === "degraded" ? "degraded" : "unknown";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[0.7rem] font-medium",
        tone,
      )}
      title={`API status: ${label}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        {status === "ok" && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
        )}
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      db: {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Composer
// ---------------------------------------------------------------------------

interface ComposerProps {
  composer: string;
  isRunning: boolean;
  disabled: boolean;
  textareaRef: React.RefObject<HTMLTextAreaElement | null>;
  onChange: (e: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSubmit: (e: FormEvent<HTMLFormElement>) => void;
  onCancel: () => void;
  showCancel: boolean;
  showTimer: boolean;
  turnElapsed: number;
  historyLoading: boolean;
  historyError: string | null;
}

function Composer({
  composer,
  isRunning,
  disabled,
  textareaRef,
  onChange,
  onKeyDown,
  onSubmit,
  onCancel,
  showCancel,
  showTimer,
  turnElapsed,
  historyLoading,
  historyError,
}: ComposerProps) {
  const canSend = !isRunning && composer.trim().length > 0;
  return (
    <form
      onSubmit={onSubmit}
      className={cn(
        "flex flex-col gap-2 border-t border-[color:var(--color-border)] bg-[color:var(--color-card)]/60 px-4 py-3 backdrop-blur-sm sm:px-6",
      )}
      aria-busy={isRunning}
    >
      <div
        className={cn(
          "flex items-end gap-2 rounded-2xl border bg-[color:var(--color-background)] p-1.5",
          "transition-colors focus-within:border-[color:var(--color-primary)]/50",
          "border-[color:var(--color-border)] shadow-sm",
        )}
      >
        <label htmlFor="chat-composer" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-composer"
          ref={textareaRef}
          value={composer}
          onChange={onChange}
          onKeyDown={onKeyDown}
          placeholder="Ask a question about NBA stats…"
          rows={2}
          disabled={disabled}
          className={cn(
            "min-h-[2.5rem] flex-1 resize-y rounded-xl bg-transparent px-3 py-2 text-sm",
            "text-[color:var(--color-foreground)] placeholder:text-[color:var(--color-muted-foreground)]",
            "focus-visible:outline-none disabled:opacity-50",
          )}
        />
        <div className="flex items-center gap-1">
          {isRunning && !showCancel ? (
            <Button type="button" variant="ghost" size="icon" disabled aria-label="Running">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            </Button>
          ) : null}
          <Button
            type="submit"
            variant="primary"
            size="icon"
            disabled={!canSend}
            aria-label="Send message"
          >
            {isRunning ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="h-4 w-4" aria-hidden="true" />
            )}
          </Button>
        </div>
      </div>

      {/* The single, labeled Cancel affordance (appears after the §13 5s
          threshold). Exactly one button is named "Cancel" so the e2e
          error-path test's /^cancel$/i selector stays unambiguous. */}
      {showCancel && (
        <div className="flex justify-end">
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            <Square className="h-3 w-3" aria-hidden="true" />
            Cancel
          </Button>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-[color:var(--color-muted-foreground)]">
        <span className="inline-flex items-center gap-1.5">
          <kbd className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 font-sans text-[0.65rem]">
            Enter
          </kbd>
          to send
          <span className="opacity-40">·</span>
          <kbd className="rounded border border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-1.5 py-0.5 font-sans text-[0.65rem]">
            Shift+Enter
          </kbd>
          for newline
          {historyLoading ? <span className="opacity-70">· loading history…</span> : null}
          {historyError !== null ? (
            <span className="text-[color:var(--color-danger-fg)]">
              · history error: {historyError}
            </span>
          ) : null}
        </span>
        {showTimer && (
          <span className="inline-flex items-center gap-1">
            <Timer className="h-3 w-3" aria-hidden="true" />
            {formatElapsed(turnElapsed)}
          </span>
        )}
      </div>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Motion banner wrapper
// ---------------------------------------------------------------------------

function AnimateBanner({ show, children }: { show: boolean; children: React.ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <motion.div
      initial={reduce ? false : { height: 0, opacity: 0 }}
      animate={{
        height: show ? "auto" : 0,
        opacity: show ? 1 : 0,
      }}
      transition={reduce ? { duration: 0 } : { type: "spring", stiffness: 320, damping: 32 }}
      style={{ overflow: "hidden" }}
      aria-hidden={!show}
    >
      {show ? children : null}
    </motion.div>
  );
}
