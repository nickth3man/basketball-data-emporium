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
import type { ChangeEvent, FormEvent, KeyboardEvent, SetStateAction } from "react";
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

import { ChatTimeline, type TimelineMessage, type TimelineStatus } from "@/components/ChatTimeline";
import { ClarifyPrompt } from "@/components/ClarifyPrompt";
import { ClearHistoryButton } from "@/components/ClearHistoryButton";
import { CommandMenu } from "@/components/CommandMenu";
import { Button } from "@/components/ui/Button";
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

function settledAnswer(state: ChatTurnState): string {
  if (state.answer.length > 0) return state.answer;
  return state.status === "error" ? "(error: no answer)" : "(no answer)";
}

function timelineStatus(
  isRunning: boolean,
  showError: boolean,
  showCancelled: boolean,
  turnStatus: ChatTurnState["status"],
): TimelineStatus {
  if (isRunning) return "running";
  if (showError) return "error";
  if (showCancelled) return "cancelled";
  return turnStatus === "done" ? "done" : "idle";
}

function findLastMessage(
  messages: TimelineMessage[],
  role: TimelineMessage["role"],
): TimelineMessage | undefined {
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (message?.role === role) return message;
  }
  return undefined;
}

type SetSessionMessages = (sessionId: string, update: SetStateAction<TimelineMessage[]>) => void;

function useSessionHistory(sessionId: string | null) {
  const [messages, setMessagesState] = useState<TimelineMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const messagesSessionIdRef = useRef<string | null>(null);
  const locallyModifiedSessionIdRef = useRef<string | null>(null);
  const requestIdRef = useRef(0);

  const setMessages = useCallback<SetSessionMessages>((targetSessionId, update) => {
    const sameSession = messagesSessionIdRef.current === targetSessionId;
    messagesSessionIdRef.current = targetSessionId;
    locallyModifiedSessionIdRef.current = targetSessionId;
    setMessagesState((previousMessages) => {
      const baseMessages = sameSession ? previousMessages : [];
      return typeof update === "function" ? update(baseMessages) : update;
    });
  }, []);

  useEffect(() => {
    const requestId = ++requestIdRef.current;
    if (sessionId === null) {
      messagesSessionIdRef.current = null;
      locallyModifiedSessionIdRef.current = null;
      setMessagesState([]);
      setError(null);
      setLoading(false);
      return;
    }

    let cancelled = false;
    if (messagesSessionIdRef.current !== sessionId) {
      messagesSessionIdRef.current = sessionId;
      locallyModifiedSessionIdRef.current = null;
      setMessagesState([]);
    }
    setLoading(true);
    setError(null);
    getSessionHistory(sessionId)
      .then((page) => {
        // History is hydration, not a last-writer-wins update. Once this
        // session has local user/assistant messages, those stay authoritative.
        if (
          !cancelled &&
          requestId === requestIdRef.current &&
          messagesSessionIdRef.current === sessionId &&
          locallyModifiedSessionIdRef.current !== sessionId
        ) {
          setMessagesState(historyToTimeline(page.messages));
        }
      })
      .catch((historyError: unknown) => {
        if (!cancelled && requestId === requestIdRef.current) {
          setError(historyError instanceof Error ? historyError.message : String(historyError));
        }
      })
      .finally(() => {
        if (!cancelled && requestId === requestIdRef.current) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  return { messages, setMessages, historyError: error, historyLoading: loading };
}

function useTurnElapsed(isRunning: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  const startedAtRef = useRef<number | null>(null);

  useEffect(() => {
    if (!isRunning) return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [isRunning]);

  useEffect(() => {
    startedAtRef.current = isRunning ? (startedAtRef.current ?? Date.now()) : null;
  }, [isRunning]);

  return isRunning && startedAtRef.current !== null ? now - startedAtRef.current : 0;
}

function useTurnSettlement(
  state: ChatTurnState,
  reset: () => void,
  sessionId: string | null,
  setMessages: SetSessionMessages,
) {
  const [lastError, setLastError] = useState<ChatTurnError | null>(null);
  const [lastCancelled, setLastCancelled] = useState(false);

  const clearNotice = useCallback(() => {
    setLastError(null);
    setLastCancelled(false);
  }, []);

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
      clearNotice();
    }

    if (state.status === "cancelled") {
      reset();
      return;
    }

    if (sessionId === null) {
      reset();
      return;
    }

    const answer = settledAnswer(state);
    setMessages(sessionId, (previousMessages) => {
      const previous = previousMessages.at(-1);
      if (previous?.role === "assistant" && previous.content === answer) return previousMessages;
      return [...previousMessages, { role: "assistant", content: answer, turn: state }];
    });
    reset();
  }, [clearNotice, reset, sessionId, setMessages, state]);

  return { lastError, lastCancelled, clearNotice };
}

export function ChatView() {
  const { sessions, create, health } = useSessions();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const { messages, setMessages, historyError, historyLoading } = useSessionHistory(sessionId);
  const [composer, setComposer] = useState<string>("");
  const [paletteOpen, setPaletteOpen] = useState<boolean>(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (sessionId !== null) return;
    if (sessions.length > 0 && sessions[0]) {
      setSessionId(sessions[0].id);
    }
  }, [sessions, sessionId]);

  const { state, send, cancel, reset } = useChatTurn(sessionId);
  const { lastError, lastCancelled, clearNotice } = useTurnSettlement(
    state,
    reset,
    sessionId,
    setMessages,
  );

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

      clearNotice();

      setMessages(sid, (prev) => [...prev, { role: "user", content: trimmed }]);
      setComposer("");
      await send(trimmed, sid);
    },
    [sessionId, create, send, clearNotice, setMessages],
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

  const handleCleared = useCallback(() => {
    if (sessionId !== null) setMessages(sessionId, []);
    clearNotice();
  }, [clearNotice, sessionId, setMessages]);

  const isRunning = state.status === "running";
  const turnElapsed = useTurnElapsed(isRunning);
  const showTimer = isRunning && turnElapsed >= TIMER_AFTER_MS;
  const showCancel = isRunning && turnElapsed >= CANCEL_AFTER_MS;

  const retryText = useMemo(() => {
    const lastUser = findLastMessage(messages, "user");
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

  const clarification = state.status === "awaiting_clarification" ? state.clarification : null;
  const showErrorBanner = lastError !== null && !isRunning;
  const showCancelledNote = lastCancelled && lastError === null && !isRunning;
  const currentTimelineStatus = timelineStatus(
    isRunning,
    showErrorBanner,
    showCancelledNote,
    state.status,
  );

  // Command palette wiring.
  const handleCopyLastAnswer = useCallback((): boolean => {
    const lastAssistant = findLastMessage(messages, "assistant");
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

  const handlePickQuestion = useCallback((question: string) => {
    setComposer(question);
    textareaRef.current?.focus();
  }, []);

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
        onPickExample={handlePickQuestion}
        status={currentTimelineStatus}
      />

      <ClarificationPanel clarification={clarification} onAnswer={handleSubmit} />
      <TurnErrorBanner
        error={showErrorBanner ? lastError : null}
        retryText={retryText}
        retryAriaLabel={retryAriaLabel}
        onRetry={handleRetry}
      />
      <CancelledBanner
        show={showCancelledNote}
        retryText={retryText}
        retryAriaLabel={retryAriaLabel}
        onRetry={handleRetry}
      />

      <Composer
        composer={composer}
        isRunning={isRunning}
        textareaRef={textareaRef}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onSubmit={handleFormSubmit}
        onCancel={cancel}
        showCancel={showCancel}
        showTimer={showTimer}
        turnElapsed={turnElapsed}
        historyLoading={historyLoading}
        historyError={historyError}
      />

      <CommandMenu
        examples={EXAMPLE_QUESTIONS}
        onPickQuestion={handlePickQuestion}
        onCopyLastAnswer={handleCopyLastAnswer}
        onClearHistory={handleClearFromPalette}
        hasHistory={hasHistory}
        open={paletteOpen}
        onOpenChange={setPaletteOpen}
      />
    </div>
  );
}

interface ClarificationPanelProps {
  clarification: ChatTurnState["clarification"];
  onAnswer: (text: string) => Promise<void>;
}

function ClarificationPanel({ clarification, onAnswer }: ClarificationPanelProps) {
  if (!clarification) return null;

  return (
    <div className="border-t border-border bg-muted/40 px-4 py-3 sm:px-6">
      <p role="status" aria-live="polite" className="mb-2 text-sm text-muted-foreground">
        Your answer is needed to continue.
      </p>
      <ClarifyPrompt
        question={clarification.question}
        options={clarification.options ?? null}
        disabled={false}
        onAnswer={(text) => void onAnswer(text)}
      />
    </div>
  );
}

interface RetryBannerProps {
  retryText: string;
  retryAriaLabel: string;
  onRetry: () => void;
}

interface TurnErrorBannerProps extends RetryBannerProps {
  error: ChatTurnError | null;
}

function TurnErrorBanner({ error, retryText, retryAriaLabel, onRetry }: TurnErrorBannerProps) {
  const text = error ? errorBannerText(error) : null;

  return (
    <AnimateBanner show={error !== null}>
      {error && text && (
        <div
          role="alert"
          aria-live="assertive"
          className={cn(
            `flex items-center justify-between gap-3 border-t px-4 py-3 sm:px-6`,
            "border-danger-border bg-danger-bg",
          )}
        >
          <div className="flex items-start gap-2.5">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-danger-fg" aria-hidden="true" />
            <div className="flex flex-col gap-0.5">
              <p className="font-medium text-danger-fg">{text.title}</p>
              {text.detail.length > 0 && (
                <p className="text-xs text-danger-fg opacity-80">{text.detail}</p>
              )}
              <p className="text-[0.7rem] text-danger-fg opacity-60">Code: {error.code}</p>
            </div>
          </div>
          <RetryButton retryText={retryText} retryAriaLabel={retryAriaLabel} onRetry={onRetry} />
        </div>
      )}
    </AnimateBanner>
  );
}

interface CancelledBannerProps extends RetryBannerProps {
  show: boolean;
}

function CancelledBanner({ show, retryText, retryAriaLabel, onRetry }: CancelledBannerProps) {
  return (
    <AnimateBanner show={show}>
      <div
        role="status"
        aria-live="polite"
        className={cn(
          `flex items-center justify-between gap-3 border-t px-4 py-2.5 sm:px-6`,
          "border-border bg-muted",
          "text-sm text-muted-foreground",
        )}
      >
        <span className="inline-flex items-center gap-2">
          <Ban className="size-3.5" aria-hidden="true" />
          Cancelled.
        </span>
        <RetryButton retryText={retryText} retryAriaLabel={retryAriaLabel} onRetry={onRetry} />
      </div>
    </AnimateBanner>
  );
}

function RetryButton({ retryText, retryAriaLabel, onRetry }: RetryBannerProps) {
  return (
    <Button
      type="button"
      variant="subtle"
      size="sm"
      disabled={retryText.length === 0}
      onClick={onRetry}
      aria-label={retryAriaLabel}
    >
      <RotateCw className="size-3.5" aria-hidden="true" />
      Retry
    </Button>
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
    <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-card/60 px-4 py-3 backdrop-blur-sm sm:px-6">
      <div className="flex items-center gap-3">
        <div
          aria-hidden="true"
          className="flex size-9 items-center justify-center rounded-xl bg-(--color-primary)/12 text-(--color-primary) ring-1 ring-(--color-primary)/25 ring-inset"
        >
          {/* Baller brand mark — bold Oswald "B" monogram tile. */}
          <span className="font-display text-base leading-none font-bold">B</span>
        </div>
        <div className="flex flex-col">
          <h1 className="font-display text-base/tight font-semibold tracking-tight">
            Basketball Data Chatbot
          </h1>
          <p className="text-xs text-muted-foreground">
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
            `inline-flex h-8 items-center gap-1 rounded-lg border border-border`,
            `bg-card px-2 text-xs text-muted-foreground`,
            `transition-colors hover:bg-muted hover:text-(--color-foreground)`,
            `focus-visible:ring-2 focus-visible:ring-(--color-ring) focus-visible:ring-offset-2 focus-visible:ring-offset-card focus-visible:outline-none`,
          )}
        >
          <CommandIcon className="size-3.5" aria-hidden="true" />
          <kbd className="font-sans text-[0.7rem]">⌘K</kbd>
        </button>
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
        `inline-flex items-center gap-1.5 rounded-lg border px-2 py-1 text-[0.7rem] font-medium`,
        tone,
      )}
      title={`API status: ${label}`}
    >
      <span className="relative flex size-1.5">
        {status === "ok" && (
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-current opacity-60" />
        )}
        <span className="relative inline-flex size-1.5 rounded-full bg-current" />
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
        `flex flex-col gap-2 border-t border-border bg-card/60 px-4 py-3 backdrop-blur-sm sm:px-6`,
      )}
      aria-busy={isRunning}
    >
      <div
        className={cn(
          `flex items-end gap-2 rounded-2xl border bg-background p-1.5`,
          `transition-colors focus-within:border-(--color-primary)/50`,
          "border-border shadow-sm",
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
          disabled={isRunning}
          className={cn(
            `min-h-10 flex-1 resize-y rounded-xl bg-transparent px-3 py-2 text-sm`,
            `text-(--color-foreground) placeholder:text-muted-foreground`,
            `focus-visible:outline-none disabled:opacity-50`,
          )}
        />
        <div className="flex items-center gap-1">
          {isRunning && !showCancel ? (
            <Button type="button" variant="ghost" size="icon" disabled aria-label="Running">
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
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
              <Loader2 className="size-4 animate-spin" aria-hidden="true" />
            ) : (
              <Send className="size-4" aria-hidden="true" />
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
            <Square className="size-3" aria-hidden="true" />
            Cancel
          </Button>
        </div>
      )}

      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <kbd className="rounded-sm border border-border bg-muted px-1.5 py-0.5 font-sans text-[0.65rem]">
            Enter
          </kbd>
          to send
          <span className="opacity-40">·</span>
          <kbd className="rounded-sm border border-border bg-muted px-1.5 py-0.5 font-sans text-[0.65rem]">
            Shift+Enter
          </kbd>
          for newline
          {historyLoading ? <span className="opacity-70">· loading history…</span> : null}
          {historyError !== null ? (
            <span className="text-danger-fg">· history error: {historyError}</span>
          ) : null}
        </span>
        {showTimer && (
          <span className="inline-flex items-center gap-1">
            <Timer className="size-3" aria-hidden="true" />
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
