/**
 * ClarifyPrompt (PLAN §8.3).
 *
 * Renders the `clarification_needed` SSE event: the question the agent
 * asked, plus its suggested options (if any) as buttons, and a free-text
 * fallback input. All paths funnel into the same `onAnswer(text)`
 * callback, so the parent (`ChatView`) can re-send the answer through
 * the same `useChatTurn.send()` pipeline.
 *
 * The free-text fallback is a single-line input with an Enter-to-submit
 * shortcut; the parent owns the broader chat input, this is just a
 * focused, context-specific reply box for the clarification.
 */
import { useCallback, useState, type FormEvent } from "react";

import { Button } from "@/components/ui/Button";

export interface ClarifyPromptProps {
  question: string;
  options: string[] | null;
  onAnswer: (text: string) => void;
  /** Disable the prompt while a turn is running. */
  disabled?: boolean;
}

export function ClarifyPrompt({ question, options, onAnswer, disabled = false }: ClarifyPromptProps) {
  const [freeText, setFreeText] = useState<string>("");

  const handleFreeSubmit = useCallback(
    (e: FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = freeText.trim();
      if (trimmed.length === 0) return;
      onAnswer(trimmed);
      setFreeText("");
    },
    [freeText, onAnswer],
  );

  return (
    <fieldset
      aria-label="Clarification needed"
      className="flex flex-col gap-3 rounded border border-amber-300 bg-amber-50 p-3 text-sm"
    >
      <legend className="sr-only">Clarification needed</legend>
      <p className="font-medium text-amber-900">{question}</p>
      {options !== null && options.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {options.map((opt) => (
            <li key={opt}>
              <Button
                type="button"
                variant="subtle"
                disabled={disabled}
                onClick={() => {
                  onAnswer(opt);
                }}
              >
                {opt}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form className="flex items-center gap-2" onSubmit={handleFreeSubmit}>
        <label htmlFor="clarify-freetext" className="sr-only">
          Your answer
        </label>
        <input
          id="clarify-freetext"
          type="text"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          placeholder="Or type your own answer…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded border border-amber-300 bg-[color:var(--color-background)] px-2 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-amber-50 disabled:opacity-50"
        />
        <Button type="submit" variant="primary" disabled={disabled || freeText.trim().length === 0}>
          Send
        </Button>
      </form>
    </fieldset>
  );
}