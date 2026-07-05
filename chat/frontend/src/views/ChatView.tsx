/**
 * ChatView (PLAN §8.3, §13, §15 Phase 5 exit).
 *
 * The composition root for the chat tab. Owns:
 *   - The active session (lifecycle via `useSessions`).
 *   - The message timeline (history loaded from `getSessionHistory`,
 *     in-flight assistant turns appended as `useChatTurn` settles).
 *   - The composer (textarea + Send, Enter to send / Shift+Enter newline).
 *   - The Cancel affordance when a turn runs > 5 s (§13).
 *   - The elapsed timer (visible while running > 1 s).
 *   - The error banner with a retry affordance.
 *   - The clarification prompt (when `state.clarification` is set).
 *
 * The actual SSE plumbing lives in the parallel fixer's `useChatTurn`;
 * this component is the wiring contract per the Phase 5 spec.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";

import { ChatTimeline, type TimelineMessage } from "@/components/ChatTimeline";
import { ClarifyPrompt } from "@/components/ClarifyPrompt";
import { ClearHistoryButton } from "@/components/ClearHistoryButton";
import { Button } from "@/components/ui/Button";
import { getSessionHistory, type SessionMessage } from "@/api/client";
import { useChatTurn, type ChatTurnState } from "@/hooks/useChatTurn";
import { useSessions, type HealthStatus } from "@/hooks/useSessions";

/** Show the cancel button after this many seconds of a running turn (§13). */
const CANCEL_AFTER_MS = 5_000;
/** Show the elapsed timer after this many seconds (§13). */
const TIMER_AFTER_MS = 1_000;

function historyToTimeline(messages: SessionMessage[]): TimelineMessage[] {
  // Server returns messages oldest-first; the timeline renders top-down.
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

export function ChatView() {
  const { sessions, create, health } = useSessions();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<TimelineMessage[]>([]);
  const [composer, setComposer] = useState<string>("");
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState<boolean>(false);
  const [now, setNow] = useState<number>(() => Date.now());
  const turnStartedAtRef = useRef<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Pick (or create) the active session lazily.
  useEffect(() => {
    if (sessionId !== null) return;
    if (sessions.length > 0 && sessions[0]) {
      setSessionId(sessions[0].id);
    }
  }, [sessions, sessionId]);

  // Load history whenever the session changes.
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

  // Drive `useChatTurn` against the active session. `retryNonce` lets
  // the user re-send the last message without re-typing (Phase 5 §13
  // error UX — the retry button bumps this counter).
  const { state, send, cancel, reset } = useChatTurn(sessionId);

  // Tick the elapsed-time display while a turn runs.
  useEffect(() => {
    if (state.status !== "running") return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [state.status]);

  // When the turn settles (done / error / cancelled), fold the final
  // state into the timeline as an assistant message and reset the
  // hook so the next send starts clean.
  const settleAnswer = useCallback((s: ChatTurnState) => {
    const answer =
      s.answer.length > 0
        ? s.answer
        : s.status === "error"
          ? "(error: no answer)"
          : "(no answer)";
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === "assistant" && last.content === answer) return prev;
      return [
        ...prev,
        {
          role: "assistant",
          content: answer,
          turn: s,
        },
      ];
    });
    reset();
  }, [reset]);

  useEffect(() => {
    if (state.status !== "done" && state.status !== "error" && state.status !== "cancelled") {
      return;
    }
    settleAnswer(state);
  }, [state, settleAnswer]);

  const handleSubmit = useCallback(
    async (text: string): Promise<void> => {
      const trimmed = text.trim();
      if (trimmed.length === 0) return;

      // Make sure we have a session — create one on first send (Phase 5
      // exit: "one simple question works end-to-end"). The create is
      // awaited so the very first SSE turn carries the real session id.
      let sid = sessionId;
      if (sid === null) {
        const created = await create(null);
        sid = created.id;
        setSessionId(sid);
      }

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
      // Enter to send, Shift+Enter for newline (§8.4).
      if (e.key === "Enter" && !e.shiftKey) {
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
  }, []);

  const isRunning = state.status === "running";

  // Track when the current running turn began so we can show an accurate
  // elapsed timer + honor the "cancel after 5s" UX rule (§13). The hook
  // doesn't expose a turn-started timestamp, so we capture it ourselves
  // whenever we transition into the "running" state.
  useEffect(() => {
    if (state.status === "running") {
      if (turnStartedAtRef.current === null) {
        turnStartedAtRef.current = Date.now();
      }
    } else {
      turnStartedAtRef.current = null;
    }
  }, [state.status]);
  const turnElapsed = isRunning && turnStartedAtRef.current !== null ? now - turnStartedAtRef.current : 0;
  const showTimer = isRunning && turnElapsed >= TIMER_AFTER_MS;
  const showCancel = isRunning && turnElapsed >= CANCEL_AFTER_MS;

  const retryText = useMemo(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    return lastUser?.content ?? "";
  }, [messages]);

  const handleRetry = useCallback(() => {
    void handleSubmit(retryText);
  }, [handleSubmit, retryText]);

  const showClarify =
    state.status === "running" &&
    state.clarification !== null &&
    state.clarification !== undefined;
  const showError = state.status === "error" && state.error !== null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[color:var(--color-border)] px-4 py-3">
        <div className="flex flex-col">
          <h1 className="text-base font-semibold tracking-tight">
            Basketball Data Chatbot
          </h1>
          <p className="text-xs text-[color:var(--color-muted-foreground)]">
            Answers grounded in the warehouse — never from model memory.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <HealthBadge status={health} />
          {sessionId !== null && (
            <ClearHistoryButton sessionId={sessionId} onCleared={handleCleared} />
          )}
        </div>
      </header>

      <ChatTimeline messages={messages} liveTurn={isRunning || showError ? state : null} />

      {showClarify && state.clarification && (
        <div className="border-t border-[color:var(--color-border)] px-4 py-3">
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

      {showError && state.error && (
        <div
          role="alert"
          className="flex items-center justify-between gap-3 border-t border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900"
        >
          <div>
            <p className="font-medium">
              {state.error.code === "cancelled"
                ? "Turn cancelled."
                : `Error: ${state.error.message}`}
            </p>
            <p className="text-xs opacity-80">Code: {state.error.code}</p>
          </div>
          <Button
            type="button"
            variant="subtle"
            disabled={retryText.length === 0}
            onClick={handleRetry}
          >
            Retry
          </Button>
        </div>
      )}

      <form
        onSubmit={handleFormSubmit}
        className="flex flex-col gap-2 border-t border-[color:var(--color-border)] px-4 py-3"
      >
        <div className="flex items-end gap-2">
          <label htmlFor="chat-composer" className="sr-only">
            Message
          </label>
          <textarea
            id="chat-composer"
            ref={textareaRef}
            value={composer}
            onChange={handleChange}
            onKeyDown={handleKeyDown}
            placeholder="Ask a question about NBA stats…"
            rows={2}
            disabled={isRunning}
            className="min-h-[2.5rem] flex-1 resize-y rounded border border-[color:var(--color-border)] bg-[color:var(--color-background)] px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-background)] disabled:opacity-50"
          />
          <div className="flex flex-col gap-1">
            <Button
              type="submit"
              variant="primary"
              disabled={isRunning || composer.trim().length === 0}
            >
              {isRunning ? "Running…" : "Send"}
            </Button>
            {showCancel && (
              <Button type="button" variant="ghost" onClick={handleCancel}>
                Cancel
              </Button>
            )}
          </div>
        </div>
        <div className="flex items-center justify-between text-xs text-[color:var(--color-muted-foreground)]">
          <span>
            Enter to send · Shift+Enter for newline
            {historyLoading ? " · loading history…" : ""}
            {historyError !== null ? ` · history error: ${historyError}` : ""}
          </span>
          {showTimer && <span>Running for {formatElapsed(turnElapsed)}</span>}
        </div>
      </form>
    </div>
  );
}

interface HealthBadgeProps {
  status: HealthStatus;
}

function HealthBadge({ status }: HealthBadgeProps) {
  const tone =
    status === "ok"
      ? "bg-emerald-100 text-emerald-800"
      : status === "degraded"
        ? "bg-amber-100 text-amber-800"
        : "bg-zinc-200 text-zinc-700";
  const dot =
    status === "ok" ? "🟢" : status === "degraded" ? "🟡" : "⚪";
  const label = status === "ok" ? "connected" : status === "degraded" ? "degraded" : "unknown";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${tone}`}
      title={`API status: ${label}`}
    >
      <span aria-hidden="true">{dot}</span>
      db: {label}
    </span>
  );
}