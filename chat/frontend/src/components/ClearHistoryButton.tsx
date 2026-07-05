/**
 * ClearHistoryButton (PLAN §8.3).
 *
 * Confirms then calls `DELETE /api/sessions/{id}`, then fires the
 * `onCleared` callback so the parent can reset its local timeline
 * state. Disabled while the request is in flight.
 *
 * Confirmation strategy: a native `window.confirm()` is intentionally
 * used for v1 (the plan notes "simpler and accessible enough"). The
 * `confirm` dialog is keyboard-friendly, blocks accidental clicks, and
 * keeps the component a single button — no inline state machine needed.
 * A later polish pass can replace it with an inline two-step confirm
 * (button → "Are you sure? [Yes][No]") without changing the public API.
 */
import { useState } from "react";

import { deleteSession } from "@/api/client";

import { Button } from "@/components/ui/Button";

export interface ClearHistoryButtonProps {
  sessionId: string;
  onCleared: () => void;
  /** Optional click label override. */
  label?: string;
}

export function ClearHistoryButton({
  sessionId,
  onCleared,
  label = "Clear history",
}: ClearHistoryButtonProps) {
  const [busy, setBusy] = useState<boolean>(false);

  const handleClick = async (): Promise<void> => {
    const confirmed = window.confirm(
      "Clear this session's visible chat history? The underlying debug logs are kept.",
    );
    if (!confirmed) return;
    setBusy(true);
    try {
      await deleteSession(sessionId);
      onCleared();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button type="button" variant="ghost" disabled={busy} onClick={() => void handleClick()}>
      {busy ? "Clearing…" : label}
    </Button>
  );
}