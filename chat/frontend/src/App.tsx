/**
 * Phase 2 typed-wiring demo (PLAN §15 Phase 2 exit: "frontend can create
 * a session and read history with full types").
 *
 * This is intentionally NOT the chat shell — that lands in Phase 5
 * (`ChatTimeline`, `useChatTurn` SSE, `ResultTable`, `EvidenceCard`, …).
 * Right now the page just exercises the openapi-fetch + openapi-typescript
 * chain end-to-end against the FastAPI sessions REST surface, so we can
 * validate type safety before Phase 5 builds the real UX on top of it.
 */
import { useState } from "react";

import {
  getSessionHistory,
  type HistoryPage,
  type SessionMeta,
} from "@/api/client";
import { useSessions, type HealthStatus } from "@/hooks/useSessions";

export function App() {
  const { sessions, loading, health, create, clearHistory } = useSessions();
  const [newSession, setNewSession] = useState<SessionMeta | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyById, setHistoryById] = useState<Record<string, HistoryPage>>({});
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);

  const handleCreate = async (): Promise<void> => {
    setBusy(true);
    try {
      const session = await create();
      setNewSession(session);
    } finally {
      setBusy(false);
    }
  };

  const handleLoadHistory = async (id: string): Promise<void> => {
    setHistoryError(null);
    setPendingId(id);
    try {
      const page = await getSessionHistory(id);
      setHistoryById((prev) => ({ ...prev, [id]: page }));
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setHistoryError(message);
    } finally {
      setPendingId(null);
    }
  };

  const handleClear = async (id: string): Promise<void> => {
    setBusy(true);
    try {
      await clearHistory(id);
      setHistoryById((prev) => {
        const next = { ...prev };
        delete next[id];
        return next;
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col gap-6 px-6 py-12">
      <header className="flex flex-col items-start gap-2">
        <h1 className="text-3xl font-semibold tracking-tight">
          Basketball Data Chatbot
        </h1>
        <p className="text-sm text-[color:var(--color-muted-foreground)]">
          Phase 2 typed-wiring demo — the chat shell arrives in Phase 5.
        </p>
        <HealthBadge status={health} />
      </header>

      <section className="flex flex-col gap-3 rounded border border-[color:var(--color-border)] p-4">
        <h2 className="text-lg font-medium">Sessions</h2>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={handleCreate}
            disabled={busy}
            className="rounded bg-[color:var(--color-primary)] px-3 py-1.5 text-sm font-medium text-[color:var(--color-primary-foreground)] disabled:opacity-50"
          >
            New session
          </button>
          <span className="text-sm text-[color:var(--color-muted-foreground)]">
            {loading ? "refreshing…" : `${sessions.length} known`}
          </span>
        </div>

        {newSession !== null && (
          <p className="text-sm">
            Created session <code className="font-mono">{newSession.id}</code>{" "}
            (title: <em>{newSession.title}</em>)
          </p>
        )}

        {sessions.length === 0 ? (
          <p className="text-sm text-[color:var(--color-muted-foreground)]">
            No sessions yet — click <strong>New session</strong> to mint one.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                history={historyById[session.id]}
                isPending={pendingId === session.id}
                busy={busy}
                onLoadHistory={handleLoadHistory}
                onClear={handleClear}
              />
            ))}
          </ul>
        )}

        {historyError !== null && (
          <p className="text-sm text-red-600" role="alert">
            History error: {historyError}
          </p>
        )}
      </section>

      {/* ChatView lands here in Phase 5 (PLAN §8.3). */}
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

  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-xs font-medium ${tone}`}
    >
      health: {status}
    </span>
  );
}

interface SessionRowProps {
  session: SessionMeta;
  history: HistoryPage | undefined;
  isPending: boolean;
  busy: boolean;
  onLoadHistory: (id: string) => Promise<void>;
  onClear: (id: string) => Promise<void>;
}

function SessionRow({
  session,
  history,
  isPending,
  busy,
  onLoadHistory,
  onClear,
}: SessionRowProps) {
  return (
    <li className="rounded border border-[color:var(--color-border)] p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-col">
          <span className="font-medium">{session.title}</span>
          <span className="font-mono text-xs text-[color:var(--color-muted-foreground)]">
            {session.id} · {session.message_count} message
            {session.message_count === 1 ? "" : "s"} · status: {session.status}
          </span>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => {
              void onLoadHistory(session.id);
            }}
            disabled={isPending || busy}
            className="rounded border border-[color:var(--color-border)] px-2 py-1 text-xs disabled:opacity-50"
          >
            {isPending ? "Loading…" : "Load history"}
          </button>
          <button
            type="button"
            onClick={() => {
              void onClear(session.id);
            }}
            disabled={busy}
            className="rounded border border-[color:var(--color-border)] px-2 py-1 text-xs disabled:opacity-50"
          >
            Clear history
          </button>
        </div>
      </div>

      {history !== undefined && (
        <HistoryList history={history} />
      )}
    </li>
  );
}

interface HistoryListProps {
  history: HistoryPage;
}

function HistoryList({ history }: HistoryListProps) {
  if (history.messages.length === 0) {
    return (
      <p className="mt-2 text-xs text-[color:var(--color-muted-foreground)]">
        (empty history · total {history.total})
      </p>
    );
  }
  return (
    <ol className="mt-2 flex flex-col gap-1">
      {history.messages.map((msg) => (
        <li key={msg.ts + ":" + msg.content} className="text-sm">
          <span className="font-medium">{msg.role}:</span> {msg.content}
        </li>
      ))}
    </ol>
  );
}
