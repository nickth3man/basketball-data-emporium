# `chat/` Evaluation Harness

The eval suite is the safety net that replaces the 16 legacy templates. Templates guaranteed correctness by construction; the governed agent guarantees nothing by construction, so correctness must be asserted by measurement. Nothing in the v2 migration plan that deletes code should land while this suite is red.

Dataset: `nba_chatbot_evals_v2.csv` — 70 cases (50 multi-turn from the original sample conversations, 20 new single-turn cases). Each case is graded at up to three layers, cheapest first.

---

## 1. The three grading layers

**Layer 1 — Plan grading (deterministic, every case).** Run the user's turn-1 question through the real agent and grade the returned plan *object*, not its prose. Assertions, in order of severity:

1. `type(plan).__name__` maps to a mode in `acceptable_modes_turn1` — hard FAIL otherwise.
2. Mode equals `expected_answer_mode_turn1` — WARN on mismatch if still within acceptable. Warn-rate is tracked as a metric; a rising warn-rate is drift even when nothing fails.
3. For `execute_sql` plans: the SQL passes the gate, and `tables_referenced` intersects `expected_tables` (an any-of list — the agent may legitimately choose between a catalog model and a warehouse mart).
4. Over-clarify guard: on the 37 rows where `acceptable_modes_turn1 == "execute_sql"`, a clarify plan is a hard FAIL. This is the assertion that protects the soft-ambiguity design (system prompt rules 9–10) from regressing into question-spam. It is the single most important metric in the suite.

**Layer 2 — Result grading (ground truth, gold rows only).** For rows with `gold_key_values` populated, execute the plan's SQL against the warehouse and assert every gold value appears in the result set. Matching rules: names are matched case-insensitively after normalization; numbers match to the precision given in the gold (a gold of `30.1` accepts `30.12`); ordered golds (e.g. a top-5 set) assert set membership by default and order only when the gold is prefixed `ordered:`. Layer 2 is what actually licenses deleting the templates — target populating gold on at least the 20 single-turn rows plus the rows marked "Strong gold candidate" before the deletion PR merges.

**Layer 3 — Dialogue grading (LLM judge, optional, multi-turn rows only).** The original transcript columns (`assistant_reply_*`, `user_followup_*`) are retained as a *rubric*, not a gold transcript — the judge scores whether the agent's conversational behavior is in the same spirit (surfaces its interpretation, asks at most one focused question when it does clarify, states data caveats), never whether the wording matches. Judge scores are reported, never gated on. If Layer 3 ever disagrees with Layers 1–2, Layers 1–2 win.

## 2. Column reference

| Column | Meaning |
|---|---|
| `turns` | `single` = one-shot question; `multi` = scripted multi-turn conversation |
| `expected_answer_mode_turn1` | `execute_sql` \| `clarify` \| `not_answerable` — the mode the agent should pick on turn 1 |
| `acceptable_modes_turn1` | Pipe-separated modes the grader passes; outside this set is a hard FAIL |
| `expected_tables` | Any-of list of table names the SQL should reference. **Currently `TODO_VERIFY`** — fill from your catalog/warehouse |
| `gold_sql` | Hand-written, human-reviewed SQL that computes the ground truth |
| `gold_key_values` | Snapshot produced by running `gold_sql` — see §3 |
| `notes_for_grader` | Per-row intent, coverage caveats, and real-world hints |

Rows whose notes say **COVERAGE-DEPENDENT** (ABA stats, PER, play-by-play, on-off, college, coaches, awards) have deliberately wide `acceptable_modes_turn1`. After the first full run against your warehouse, pin each one: if the data exists, narrow to `execute_sql`; if it doesn't, narrow to `not_answerable` and the row becomes a regression test for the capability-boundary path.

## 3. Populating gold values — the warehouse is the source of truth

Real-world figures in `notes_for_grader` (Kareem's 38,387, the Warriors' 73-9) are orientation hints only. The warehouse may have coverage gaps or definitional differences, and the eval must test *the system against its own data*, not against Wikipedia. The workflow:

1. Write `gold_sql` by hand for a row and review it yourself — this is the human-verified step, the whole point of the layer.
2. Run a snapshot script that executes every non-empty `gold_sql` read-only and writes the key values into `gold_key_values`.
3. Eyeball the snapshot diff before committing. If a value surprises you (Kareem ≠ 38,387), you have found either a warehouse bug or a coverage gap — both are wins, and both should be resolved before the row goes live.

Re-run the snapshot only when the warehouse is rebuilt; commit the CSV so gold drift shows up in review.

## 4. Multi-turn replay goes through the real session store

For `turns == "multi"` rows, the harness must not shortcut by concatenating turns into one prompt. Create a real session, then per scripted user turn (`user_initial_question`, `user_followup_1`, `user_followup_2`): call the pipeline, let it persist `.model.jsonl` and `.clarify.json`, and feed the next scripted turn. This exercises exactly the machinery the CSV can't see — model-history trimming, clarification-state set/clear/TTL, and prompt enrichment via the clarification prefix — which is where multi-turn bugs actually live. Layer-1 mode assertions apply to turn 1; the final turn's plan is graded against Layer 2 gold where present. Use a temp `data/sessions/` dir per run.

## 5. Where each layer runs

- **CI (no LLM, deterministic, seconds):** plan-union validation fixtures, gate unit tests, SSE replay drift guard, and gold-SQL snapshot verification (`gold_sql` still produces `gold_key_values`). These catch schema and contract regressions on every commit.
- **Nightly / pre-merge for agent-affecting changes (live LLM, ~70–130 calls):** the full three-layer suite. Any PR that touches the system prompt, the plan types, the gate, or the repair loop runs it before merge. Run each case once; if flakiness becomes a problem, re-run failures once and report flaky-pass separately rather than averaging.
- **Report:** one line per run — mode accuracy, warn rate, over-clarify count (target: 0), table-selection accuracy, gold pass rate, repair-invocation rate, and `query_ref.source` split (catalog vs warehouse — the catalog-promotion signal from the architecture doc §5).

## 6. Extending the suite

Every production miss becomes a row: capture the question, pin the acceptable modes, add gold if the answer is checkable. Keep the single-turn share at or above a third, and keep at least a handful of must-clarify and must-refuse rows so all three plan types stay exercised. When a coverage-dependent row gets its data (e.g. you load play-by-play), flip its expectation the same day — stale expectations are how eval suites rot.
