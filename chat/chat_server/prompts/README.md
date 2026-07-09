# Prompt templates (v1 scaffolds)

Static text scaffolds consumed by Phase 3 of the governed-SQL migration. They are deliberately hand-authored as plain text with double-brace, Jinja-like placeholders (named slots, written inline as `name`) so a future Pydantic AI system-prompt loader can substitute them with either `str.format`-style rendering or a Jinja-style renderer, without touching the templates themselves.

These are v1 static assets. Wire them up during Phase 3. Do NOT edit `agent.py`, `pipeline.py`, `pyproject.toml`, or any Python file to consume them yet — another lane owns all Python.

## Templates

### `sql_analyst_system.md`

Purpose: the SQL-analyst agent's system prompt for Pydantic AI (the `@agent.system_prompt` decorator form, eventually replacing the inline `SYSTEM_PROMPT_TEMPLATE` in `agent.py`). Follows the LangGPT Role / Profile / Goal / Skills / Rules / Workflow skeleton.

Covers:
- "Key Changes" — the new structured `QueryPlan` fields (`answer_mode`, `question_interpretation`, `sql`, `clarification`, `result_contract`).
- "Guardrails And Runtime" — catalog-first generation, read-only DuckDB, no DDL / DML / writes, server-side `LIMIT`.
- "Conversation Behavior" — clarification over guessing, caveats only when they affect trust, conversational tone in user-facing text.
- "References Catalog" — LangGPT template reference.

Slots:
- `{{retrieved_models}}` — top-k catalog models retrieved per turn.
- `{{conversation_history}}` — trimmed model-message history for the session.
- `{{known_caveats}}` — caveats applicable to retrieved models (sourced from `meta_known_gap`).

### `repair.txt`

Purpose: the repair-loop prompt dispatched after a DuckDB dry-run failure. Shape lifted from DIN-SQL's `debuger()` 7-bullet fix-it rules plus the MAC-SQL Refiner dispatch (`sqlite_error + exception_class + question + schema` under a `MAX_ROUND` cap).

Covers:
- "Guardrails And Runtime" — DuckDB dry-run on syntax or schema errors, single repair reprompt under `MAX_ROUND`, fall back to a clarification or a grounded "I can't answer that yet".
- "References Catalog" — DIN-SQL `debuger()` + MAC-SQL Refiner entries.

Slots:
- `{{question}}` — the original user question.
- `{{schema}}` — catalog models in scope for this turn.
- `{{error}}` — the DuckDB error message plus the exception class.
- `{{broken_sql}}` — the SQL that failed to dry-run.

### `clarify.txt`

Purpose: the conversational clarification prompt rendered by the composer when `QueryPlan.answer_mode == "clarify"`. Grounded in the McGrady "similarity" example (career averages, career totals, peak season, advanced metrics, or overall blend).

Covers:
- "Conversation Behavior" — clarification flow, conversational tone, no implementation details in user-facing text.
- "Guardrails And Runtime" — Tracy McGrady example referenced as the canonical ambiguous case.

Slots:
- `{{interpretation}}` — the agent's plain-English read of the question.
- `{{options}}` — numbered list of disambiguation choices formatted by the runtime.

## Future wiring (Phase 3, lane-owned)

- `sql_analyst_system.md` — read by `agent.py`'s `@agent.system_prompt` and substituted with `ctx.deps.retrieved_models` / `ctx.deps.history` / `ctx.deps.known_caveats`.
- `repair.txt` — dispatched by `pipeline.py`'s repair loop after a DuckDB dry-run error, under the `MAX_ROUND` cap.
- `clarify.txt` — rendered by the composer when `answer_mode == "clarify"`.

Until Phase 3 lands, these files are inert text and will not affect runtime behavior.
