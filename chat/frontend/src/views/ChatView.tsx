/**
 * ChatView (PLAN §8.3, §13, §15 Phase 7 error-UX exit).
 *
 * The composition root for the chat tab. Owns:
 *   - The active session (lifecycle via `useSessions`).
 *   - The message timeline (history loaded from `getSessionHistory`,
 *     in-flight assistant turns appended as `useChatTurn` settles).
 *   - The composer (textarea + Send, Enter to send / Shift+Enter newline).
 *   - The Cancel affordance when a turn runs > 5 s (§13).
 *   - The elapsed timer (visible while running > 1 s).
 *   - The error banner with a retry affordance — branched on the
 *     `state.error.code` token so network / timeout / model-failure
 *     read distinctly to the user (§13, §15).
 *   - A muted inline "Cancelled." note (not a scary red banner) when
 *     the user clicks Cancel — the user bubble they sent remains in
 *     the timeline, the composer re-enables, and the Retry button is
 *     a one-click re-send of the last user message.
 *   - The clarification prompt (when `state.clarification` is set).
 *
 * The actual SSE plumbing lives in `useChatTurn`; this component is
 * the wiring contract per the Phase 5+7 spec.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, FormEvent, KeyboardEvent } from "react";

import { ChatTimeline, type TimelineMessage } from "@/components/ChatTimeline";
import { ClarifyPrompt } from "@/components/ClarifyPrompt";
import { ClearHistoryButton } from "@/components/ClearHistoryButton";
import { Button } from "@/components/ui/Button";
import { getSessionHistory, type SessionMessage } from "@/api/client";
import {
  useChatTurn,
  type ChatTurnError,
  type ChatTurnState,
} from "@/hooks/useChatTurn";
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

/**
 * Human-readable banner copy keyed on the structured error code
 * (PLAN §13 / §15 — error UX). The codes are emitted by the backend
 * (`chat_server/pipeline.py`) and by `useChatTurn`'s transport layer.
 *   - `network`         → fetch / SSE transport never reached the server
 *   - `query_timeout`   → backend `asyncio.wait_for` aborted the DB call
 *                        after the 300 s template-budget
 *   - everything else   → server-side error (template, SQL, agent, …);
 *                        the redacted server message is shown verbatim
 */
function errorBannerText(err: ChatTurnError): { title: string; detail: string } {
  switch (err.code) {
    case "network":
      return {
        title: "Connection lost. Check the server is running.",
        detail:
          "We couldn’t reach the chat backend. Once it’s back, hit Retry.",
      };
    case "query_timeout":
      return {
        title: "The query took too long and was cancelled (300s limit).",
        detail:
          "Try a more specific question, or break this into a smaller piece.",
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
  /**
   * Terminal markers lifted into local state so the error banner and
   * the inline cancelled note survive the `reset()` call that the
   * `settleAnswer` effect runs to re-enable the composer.
   */
  const [lastError, setLastError] = useState<ChatTurnError | null>(null);
  const [lastCancelled, setLastCancelled] = useState<boolean>(false);
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

  // Drive `useChatTurn` against the active session.
  const { state, send, cancel, reset } = useChatTurn(sessionId);

  // Tick the elapsed-time display while a turn runs.
  useEffect(() => {
    if (state.status !== "running") return;
    const interval = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(interval);
  }, [state.status]);

  /**
   * Fold the terminal `ChatTurnState` into the timeline and reset the
   * hook. Cancelled turns do NOT append an assistant bubble — the
   * user sees an inline "Cancelled." note below the composer instead,
   * keeping the timeline 1 message per turn when nothing useful ran.
   */
  const settleAnswer = useCallback(
    (s: ChatTurnState) => {
      if (s.status === "cancelled") {
        // The user bubble stays in `messages`; we don't append a
        // placeholder assistant bubble for the cancel path.
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
    },
    [reset],
  );

  // Single effect: when the turn settles, lift the terminal marker
  // (error vs cancelled vs clean-done) into local state AND fold the
  // answer into the timeline. Both transitions must happen in the same
  // effect — splitting them risks the reset() outpacing the banner.
  //
  // Critical: only the three terminal statuses flip the markers; the
  // transient "idle" that follows `settleAnswer → reset()` must NOT,
  // or the banner / note would be cleared the instant we want to
  // show it. (Initial mount is also "idle" — no-op.)
  useEffect(() => {
    if (
      state.status !== "done" &&
      state.status !== "error" &&
      state.status !== "cancelled"
    ) {
      return;
    }
    if (state.status === "error" && state.error !== null) {
      setLastError(state.error);
      setLastCancelled(false);
    } else if (state.status === "cancelled") {
      setLastCancelled(true);
      setLastError(null);
    } else {
      // status === "done": a clean happy-path turn — clear leftover
      // terminal markers so a previous error banner doesn't linger.
      setLastError(null);
      setLastCancelled(false);
    }
    settleAnswer(state);
  }, [state, settleAnswer]);

  const handleSubmit = useCallback(
    async (text: string): Promise<void> => {
      const trimmed = text.trim();
      if (trimmed.length === 0) return;

      // Make sure we have a session — create one on first send.
      let sid = sessionId;
      if (sid === null) {
        const created = await create(null);
        sid = created.id;
        setSessionId(sid);
      }

      // Clear any leftover terminal markers so the new turn starts
      // from a clean banner/note state.
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
    setLastError(null);
    setLastCancelled(false);
  }, []);

  /**
   * Compose is disabled while a turn is running; once the turn
   * settles (done / error / cancelled) the hooks reset state.status to
   * "idle", but we also rely on the lifted `lastError`/`lastCancelled`
   * flags to keep the banner/note visible during the idle gap.
   */
  const isRunning = state.status === "running";

  // Track when the current running turn began so we can show an accurate
  // elapsed timer + honor the "cancel after 5s" UX rule (§13).
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
    isRunning && turnStartedAtRef.current !== null
      ? now - turnStartedAtRef.current
      : 0;
  const showTimer = isRunning && turnElapsed >= TIMER_AFTER_MS;
  const showCancel = isRunning && turnElapsed >= CANCEL_AFTER_MS;

  /**
   * The last user message is the seed for the Retry button (one-click
   * re-send) — derived via `useMemo` from the timeline, which already
   * contains both history-loaded and just-sent user bubbles.
   */
  const retryText = useMemo(() => {
    const lastUser = [...messages].reverse().find((m) => m.role === "user");
    return lastUser?.content ?? "";
  }, [messages]);

  const handleRetry = useCallback(() => {
    if (retryText.length === 0) return;
    void handleSubmit(retryText);
  }, [handleSubmit, retryText]);

  /**
   * The accent-flavored Retry label sits inside `aria-label` (read by
   * screen readers) so the button reads "Retry: <first 40 chars>" to
   * assistive tech — the visible label stays the terse "Retry".
   */
  const retryAriaLabel = useMemo(() => {
    const snippet = retryText.replace(/\s+/g, " ").trim().slice(0, 40);
    return snippet.length > 0 ? `Retry: ${snippet}` : "Retry";
  }, [retryText]);

  const showClarify =
    state.status === "running" &&
    state.clarification !== null &&
    state.clarification !== undefined;
  const showErrorBanner = lastError !== null && !isRunning;
  const showCancelledNote = lastCancelled && lastError === null && !isRunning;
  const bannerText = lastError ? errorBannerText(lastError) : null;

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

      <ChatTimeline
        messages={messages}
        liveTurn={isRunning ? state : null}
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

      {showErrorBanner && lastError && bannerText && (
        <div
          role="alert"
          aria-live="assertive"
          className="flex items-center justify-between gap-3 border-t border-red-300 bg-red-50 px-4 py-3 text-sm text-red-900"
        >
          <div className="flex flex-col gap-0.5">
            <p className="font-medium">{bannerText.title}</p>
            {bannerText.detail.length > 0 && (
              <p className="text-xs opacity-80">{bannerText.detail}</p>
            )}
            <p className="text-xs opacity-60">Code: {lastError.code}</p>
          </div>
          <Button
            type="button"
            variant="subtle"
            disabled={retryText.length === 0}
            onClick={handleRetry}
            aria-label={retryAriaLabel}
          >
            Retry
          </Button>
        </div>
      )}

      {showCancelledNote && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center justify-between gap-3 border-t border-[color:var(--color-border)] bg-[color:var(--color-muted)] px-4 py-2 text-sm text-[color:var(--color-muted-foreground)]"
        >
          <p>Cancelled.</p>
          <Button
            type="button"
            variant="subtle"
            disabled={retryText.length === 0}
            onClick={handleRetry}
            aria-label={retryAriaLabel}
          >
            Retry
          </Button>
        </div>
      )}

      <form
        onSubmit={handleFormSubmit}
        className="flex flex-col gap-2 border-t border-[color:var(--color-border)] px-4 py-3"
        aria-busy={isRunning}
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
