/**
 * ClearHistoryButton (PLAN §8.3).
 *
 * Confirms then calls `DELETE /api/sessions/{id}`, then fires the
 * `onCleared` callback so the parent can reset its local timeline
 * state. Fires a sonner toast on success/failure. Disabled while the
 * request is in flight.
 *
 * Confirmation strategy: native `window.confirm()` (keyboard-friendly,
 * blocks accidental clicks). A later polish pass can swap to an inline
 * two-step confirm without changing the public API.
 */
import { useState } from "react";
import { Eraser, Loader2 } from "lucide-react";
import { toast } from "sonner";

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
      toast.success("History cleared");
    } catch {
      toast.error("Couldn't clear history");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      disabled={busy}
      onClick={() => void handleClick()}
    >
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
      ) : (
        <Eraser className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {busy ? "Clearing…" : label}
    </Button>
  );
}
