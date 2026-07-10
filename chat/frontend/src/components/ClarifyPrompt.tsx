/**
 * ClarifyPrompt (PLAN §8.3).
 *
 * Renders the `clarification_needed` SSE event: the question the agent
 * asked, plus its suggested options (if any) as buttons, and a free-text
 * fallback input. All paths funnel into the same `onAnswer(text)`
 * callback, so the parent (`ChatView`) can re-send the answer through
 * the same `useChatTurn.send()` pipeline.
 */
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { HelpCircle, Send } from "lucide-react";

import { Button } from "@/components/ui/Button";

export interface ClarifyPromptProps {
  question: string;
  options: string[] | null;
  onAnswer: (text: string) => void;
  /** Disable the prompt while a turn is running. */
  disabled?: boolean;
}

export function ClarifyPrompt({
  question,
  options,
  onAnswer,
  disabled = false,
}: ClarifyPromptProps) {
  const [freeText, setFreeText] = useState<string>("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const previousQuestionRef = useRef<string | null>(null);

  useEffect(() => {
    if (previousQuestionRef.current !== question) {
      inputRef.current?.focus();
      previousQuestionRef.current = question;
    }
  }, [question]);

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
      className="flex flex-col gap-3 rounded-xl border border-[color:var(--color-warn-border)] bg-[color:var(--color-warn-bg)] p-3 text-sm"
    >
      <legend className="sr-only">Clarification needed</legend>
      <p className="flex items-start gap-2 font-medium text-[color:var(--color-warn-fg)]">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span id="clarify-question">{question}</span>
      </p>
      {options !== null && options.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {options.map((opt) => (
            <li key={opt}>
              <Button
                type="button"
                variant="subtle"
                size="sm"
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
          ref={inputRef}
          type="text"
          aria-describedby="clarify-question"
          value={freeText}
          onChange={(e) => setFreeText(e.target.value)}
          placeholder="Or type your own answer…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded-lg border border-[color:var(--color-warn-border)] bg-[color:var(--color-card)] px-3 py-1.5 text-sm text-[color:var(--color-foreground)] placeholder:text-[color:var(--color-muted-foreground)] focus-visible:ring-2 focus-visible:ring-[color:var(--color-ring)] focus-visible:ring-offset-2 focus-visible:ring-offset-[color:var(--color-warn-bg)] focus-visible:outline-none disabled:opacity-50"
        />
        <Button type="submit" size="sm" disabled={disabled || freeText.trim().length === 0}>
          <Send className="h-3.5 w-3.5" aria-hidden="true" />
          Send
        </Button>
      </form>
    </fieldset>
  );
}
