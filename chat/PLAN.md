# Move Chat From Templates To Governed SQL

## Summary
Replace the current `template_id + params` chatbot flow with a hybrid governed SQL architecture. The model will generate SQL, but only against a curated NBA semantic catalog, then the server will validate, dry-run, execute read-only, and compose a conversational answer.

Research supports this direction over raw text-to-SQL: Google Cloud recommends schema retrieval, ambiguity detection, validation, and repair loops; dbt’s semantic-layer analysis argues that governed definitions reduce plausible wrong answers; MMSQL shows multi-turn clarification is essential for ambiguous analytics questions; Wren AI, PandasAI, Airflow, and AWS MCP examples all use semantic context plus SQLGlot-style guardrails.

Primary references:
[Google Cloud text-to-SQL techniques](https://cloud.google.com/blog/products/databases/techniques-for-improving-text-to-sql), [dbt semantic layer vs text-to-SQL](https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026), [MMSQL](https://mcxiaoxiao.github.io/MMSQL/), [Wren AI OSS](https://getwren.ai/oss), [Airflow SQL validation](https://github.com/apache/airflow/blob/main/providers/common/ai/src/airflow/providers/common/ai/utils/sql_validation.py), [PandasAI SQL sanitizer](https://github.com/sinaptik-ai/pandas-ai/blob/main/pandasai/helpers/sql_sanitizer.py), [AWS MCP SQLGlot validation example](https://github.com/awslabs/mcp/blob/main/src/billing-cost-management-mcp-server/awslabs/billing_cost_management_mcp_server/tools/storage_lens_tools.py).

Additional implementation references — semantic-catalog format, schema retrieval, repair loop, history persistence, clarification flow, governance hooks, and test harnesses — are catalogued in **References Catalog** below; the rollout order is in **Implementation Phasing**.

## Key Changes
- Replace `QueryPlan.template_id` with a new structured plan:
  - `answer_mode`: `execute_sql`, `clarify`, or `not_answerable`
  - `question_interpretation`: plain-English interpretation used for transparency
  - `sql`: generated DuckDB SQL when executable
  - `clarification`: question plus optional choices when ambiguity is material
  - `result_contract`: expected grain, columns, row limit, and answer style
- Add a curated semantic catalog for the model:
  - Defines allowed business models such as player career, player season, team season, games, awards, standings, shots, and head-to-head.
  - Defines approved metrics, dimensions, join paths, synonyms, caveats, and example questions.
  - Uses warehouse metadata where possible, but hand-authors basketball meaning like “career stats,” “peak,” “similarity,” “scorer,” “playmaker,” and “two-way.”
  - Author the catalog as YAML files, one per business model, in the `boringdata/boring-semantic-layer` `examples/flights.yml` shape: a `description` per field, `joins:` with `type` / `left_on` / `right_on`, and inline `# Note:` caveats. Map `meta_metric_definition` rows to measures, `meta_column_lineage` rows to join paths, and `meta_known_gap` rows to per-field caveats. DuckDB-native — no engine abstraction layer.
- Remove one-off executable templates as the primary query mechanism.
  - Existing templates may stay temporarily as regression fixtures during migration.
  - The final runtime should not require `template_id` selection.
- Add schema retrieval before generation.
  - For each user turn, retrieve only the relevant semantic models, metrics, examples, and known caveats.
  - Include conversation history so follow-ups like “overall blend” or “make it peak only” resolve correctly.
  - Implement retrieval with a LlamaIndex `NLSQLTableQueryEngine`-style `SQLTableNodeMapping` + `ObjectIndex`: embed each business model's `table_name` plus a `context_str` sourced from `meta_column_lineage`, retrieve top-k per turn. The startup-cached `SchemaContext` (`schema_context.py`) stays as the allowlist backbone; the per-turn index selects which slices of it enter the prompt.
- Use the `nba_api` `expected_data` dicts (~80 endpoint classes) as the literal table-schema catalogue from which the business models are designed, and `playervsplayer.py`'s declared result sets (Overall, OnOffCourt, ShotArea*, ShotDistance*) as the head-to-head model shape.
- Use Basketball-Reference `data.py` enums (NBA/ABA/BAA leagues, 9 deprecated teams, 7 historical divisions) as a `dim_*` completeness checklist when authoring dimensions and caveats.
- Refine `lookup_player` / `lookup_team` toward `nba_api` `find_players_by_full_name` exact-name resolution semantics, mapping onto `bridge_player_source_id` / `dim_all_players` rather than free-text ILIKE only. These tools already exist in `agent.py`; this is a precision pass, not a rewrite.

## Guardrails And Runtime
- Validate generated SQL with SQLGlot before execution:
  - Single statement only.
  - `SELECT` / `WITH` only.
  - Reject DDL, DML, `COPY`, `ATTACH`, file/network table functions, arbitrary macros, and multi-statements.
  - Allow only semantic catalog models or explicitly approved warehouse views.
  - Correctly distinguish CTE names from base tables.
  - Enforce server-side `LIMIT` and row caps.
  - Add a pre-execution semantic pass using `sqlglot.optimizer.optimize(ast, schema={...})` (qualify + annotate_types + validator) against a catalog-derived schema dict. This extends the existing `validation.py` parse / forbidden-node / allowlist gate without replacing it; SQLGlot is already installed.
  - Flag NBA star-player duplicate-row patterns (the same class as the exhibition-game phantom person ids in `dim_all_players`) using the `boring-semantic-layer` `fan_and_chasm_traps.py` approach: detect many-to-one joins that would inflate additive measures and route affected queries through a non-additive path declared in the catalog.
- Run a DuckDB dry-run before execution.
  - On deterministic syntax/schema errors, reprompt once with the validation error and retrieved schema.
  - If repair fails, ask a clarification or return a grounded “I can’t answer that yet.”
  - Dispatch the repair reprompt in the MAC-SQL Refiner shape — feed `sqlite_error + exception_class + question + schema` back to the model under a `MAX_ROUND` cap.
  - Use the DIN-SQL `debuger()` 7-bullet "fix-it rules" prompt as the repair-loop system text, parameterized by question + schema + broken SQL.
- Execute only through the existing read-only DuckDB connection.
- Expose the catalog to the agent through an `MCPSemanticModel.from_yaml(...)` bridge (after `boring-semantic-layer` `examples/example_mcp.py`, ~50 lines) giving the agent `list_models` / `get_model` / `query_model` / `get_time_range` tools bound to the curated YAML.
- Authorize every tool call through a `capabilities=[...]` hook (after `pydantic-ai-toolguard` and `pydantic-ai-shields`): allow `query_db`, deny `drop_table` and any write path; enforce cost caps and prompt-injection defenses inside the same hook. Native to Pydantic AI — no new framework.
- Compose answers from the question, interpretation, SQL result, and caveats.
  - For ambiguous questions, ask a conversational follow-up instead of guessing.
  - For the Tracy McGrady example, default path should ask whether similarity means career averages, totals, peak, advanced metrics, or blended profile.
  - If the user chooses “overall blend,” run a governed similarity query using normalized career/season metrics from the semantic catalog.

## Conversation Behavior
- Persist Pydantic AI model history using `ModelMessagesTypeAdapter`, not just visible JSONL messages.
  - Round-trip the history with `ModelMessagesTypeAdapter.validate_python(conv)` → `agent.run(..., message_history=...)` → `result.all_messages_json()` → disk (after `vstorm-co/pydantic-ai-examples` `history_processor/3_history_usage.py`). The current `sessions.py` JSONL stays as the visible-message store; this adds a parallel model-history file per session.
  - Trim long conversations with `keep_last_messages_with_tools()` (after `history_processor/5c_history_with_tools.py`); naive slicing breaks SQL agents that reference earlier tool calls.
- Store pending clarification state per session so option clicks and free-text replies continue the same turn.
  - Model the state on a small Pydantic object that carries ambiguity resolutions across turns, after the `langgraph-agent-sql` 5-node graph shape (list_tables → get_schema → generate_query ⇄ check_query → run_query) with a dedicated clarify node; the `propfinder-ai` pattern of collecting resolutions forward in state is the reference.
- Fix frontend clarification UX so clarification prompts remain answerable after streaming completes.
- Keep answers conversational:
  - Explain the interpretation briefly.
  - Show the result table when useful.
  - State caveats only when they affect trust.
  - Avoid exposing implementation details unless the user asks.

## Test Plan
- Add unit tests for SQL validation:
  - Allows valid `SELECT` and CTE queries over approved semantic models.
  - Rejects writes, DDL, multi-statements, unsafe functions, unknown tables, and disguised CTE/table-name cases.
- Add agent planning tests for the 50 sample conversations:
  - Classify each as executable, clarification-needed, or not-answerable.
  - Verify ambiguous prompts produce useful clarification options.
  - Verify follow-ups preserve context.
- Add execution tests for representative domains:
  - Player similarity, career stats, season leaders, team comparisons, awards, standings, game lookup, shooting zones, head-to-head, and playoffs vs regular season.
- Add SQL execution-correctness tests using the `tau2-bench` DB-hash-replay pattern: replay each gold SQL on a fresh warehouse copy, hash the result, and compare to the agent's result hash; score as the product of DB × COMMUNICATE × NL_ASSERTION × ACTION.
- Stand the suite up on `evalite` (same Vite/Vitest stack as `web/`): `evalite(...)` entries with `data` + `task` + `scorers`, with the free trace UI.
- Seed additional cases from the `gretelai/synthetic_text_to_sql` corpus (100K+ text-to-SQL pairs, Apache-2.0) to extend beyond the hand-curated JSON fixtures.
- Add end-to-end chat tests:
  - McGrady similarity multi-turn flow.
  - A vague follow-up like “what about playoffs?”
  - An unsupported question that should fail gracefully.
- Run `npm run typecheck`, `npm run lint`, and `npm run test` in `chat/` or the project’s actual chat package command set after implementation.

## Assumptions
- Chosen architecture: Hybrid governed SQL.
- The model may generate SQL, but never against unrestricted raw warehouse tables.
- Accuracy is more important than answering every prompt on the first turn.
- Existing templates can be used as migration references and regression examples, but not as the long-term execution interface.

## References Catalog

Grouped by plan component. Tier 1 = drop-in; Tier 2 = strong architectural reference.

### Semantic catalog (authoring format + LLM bridge)

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `boring-semantic-layer` `examples/flights.yml` | 1 | YAML authoring shape: `description` per field, `joins:` with `type`/`left_on`/`right_on`, inline `# Note:` caveats; DuckDB-native | https://github.com/boringdata/boring-semantic-layer/blob/main/examples/flights.yml |
| `boring-semantic-layer` `examples/example_mcp.py` | 1 | `MCPSemanticModel.from_yaml(...)` → `list_models`/`get_model`/`query_model`/`get_time_range` tools (~50 lines) | https://github.com/boringdata/boring-semantic-layer/blob/main/examples/example_mcp.py |

### Schema retrieval (per-turn)

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| LlamaIndex `NLSQLTableQueryEngine` | 1 | `SQLTableNodeMapping` + `ObjectIndex`: embed `table_name` + `context_str` per table, retrieve top-k per turn (`meta_column_lineage` = free context_str source) | https://github.com/run-llama/llama_index |

### SQL validation

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `tobymao/sqlglot` optimizer + `dialects/duckdb.py` | 1 | `optimize(ast, schema={...})` pre-execution semantic pass (qualify / annotate_types / validator); already installed | https://github.com/tobymao/sqlglot/tree/main/sqlglot/optimizer |
| `boring-semantic-layer` `fan_and_chasm_traps.py` | 2 | fan/chasm-trap detection for NBA star-player duplicate-row patterns (same class as phantom exhibition person ids) | https://github.com/boringdata/boring-semantic-layer |

### Dry-run + repair loop

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `MAC-SQL` Refiner (`core/agents.py`, `core/const.py`) | 1 | dispatch `sqlite_error + exception_class + question + schema` back to the LLM under a `MAX_ROUND` cap | https://github.com/wbbeyourself/MAC-SQL |
| `Few-shot-NL2SQL-with-prompting` `DIN-SQL.py` `debuger()` | 1 | 7-bullet "fix-it rules" repair-loop prompt (question + schema + broken SQL) | https://github.com/MohammadrezaPourreza/Few-shot-NL2SQL-with-prompting/blob/main/DIN-SQL.py |

### Model history persistence + trimming

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `pydantic-ai-examples` `history_processor/3_history_usage.py` | 1 | `ModelMessagesTypeAdapter.validate_python(conv)` → `run(..., message_history=...)` → `all_messages_json()` → disk | https://github.com/vstorm-co/pydantic-ai-examples |
| `pydantic-ai-examples` `history_processor/5c_history_with_tools.py` | 1 | `keep_last_messages_with_tools()` — tool-call-safe trimming (naive slicing breaks SQL agents) | https://github.com/vstorm-co/pydantic-ai-examples |

### Clarification flow

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `langgraph-agent-sql` | 1 | 5-node graph (list_tables → get_schema → generate_query ⇄ check_query → run_query) with a dedicated clarify node | https://github.com/leonardojdss/langgraph-agent-sql |
| `propfinder-ai` | 1 | Pydantic state collecting ambiguity resolutions forward across turns | https://github.com/leonachata/propfinder-ai |

### Governed tool authorization

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `pydantic-ai-toolguard` | 1 | `capabilities=[...]` hook: allow `query_db`, deny `drop_table`; approval workflows; append-only audit log | https://github.com/AgentsID-dev/pydantic-ai-toolguard |
| `pydantic-ai-shields` | 1 | cost-control + tool-permission + prompt-injection shields, native to Pydantic AI | https://github.com/vstorm-co/pydantic-ai-shields |

### Test suite

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `tau2-bench` (`docs/evaluation.md`, `tasks.json`) | 1 | DB-hash-replay: gold SQL on fresh warehouse → hash → compare to agent's result; reward = DB × COMMUNICATE × NL_ASSERTION × ACTION | https://github.com/sierra-research/tau2-bench |
| `evalite` | 1 | `evalite(data, task, scorers)` on the same Vite/Vitest stack as `web/`; free trace UI | https://github.com/mattpocock/evalite |
| `gretelai/synthetic_text_to_sql` | 1 | 100K+ text-to-SQL seed pairs (Apache-2.0) to extend hand-curated fixtures | https://huggingface.co/datasets/gretelai/synthetic_text_to_sql |

### Metric registry

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| Cube.js recipes | 2 | non-additivity flagging (additive vs non-additive); composed metrics (`homeWinPct = homeWins/homeGames`); entity-attribute-value for per-game statlines | https://github.com/cube-js/cube |
| dbt MetricFlow YAML | 2 | `{name, type: simple\|ratio\|cumulative, type_params:{measure, numerator, denominator}}` registry shape; validation target for `meta_metric_definition` rows | https://github.com/dbt-labs/metricflow |

### Business vocabulary

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| DataHub `business_glossary` bootstrap | 2 | hierarchical `nodes:` → `terms:` with `description`, `inherits`, `custom_properties`, `domain`, `owners` — pattern for "career / peak / similarity / scorer" definitions | https://github.com/datahub-project/datahub/tree/master/metadata-ingestion/examples/bootstrap_data |

### Few-shot + repair-dataset tooling

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| DSPy `GenerateSQL` + `BootstrapFewShot` | 2 | auto-tune few-shot demos against `web/test/fixtures/` question→SQL pairs; `(question, sql_query, error → refined_sql)` repair signature | https://github.com/stanfordnlp/dspy |
| PremSQL `ErrorDatasetGenerator` | 2 | run generator against executor → harvest errors → format with self-correction prompt → fine-tune dataset | https://github.com/premAI-io/premsql |

### Prompt skeleton + DuckDB-native audits

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| LangGPT template | 2 | Role/Profile/Goal/Skills/Rules/Workflow skeleton for the SQL-analyst system prompt | https://github.com/langgptai/LangGPT |
| SQLMesh `examples/sushi` | 2 | DuckDB-native `audits/` (`UNIQUE_VALUES`, `NOT_NULL` macros) + `tests/` YAML fixtures; `sqlmesh plan --explain` as dry-run model query without inserting results | https://github.com/SQLMesh/sqlmesh |

### Reference governance architectures + SQL-RAG blueprint

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `bonnard` (Cube.js + DuckDB + MCP) | 2 | governed DuckDB exposure over Bearer-token MCP; cube/view YAML → deploy | https://github.com/bonnard-data/bonnard |
| OrionBelt Semantic Layer | 2 | YAML semantic models → AST-compiled SQL across dialects incl DuckDB; `obsl validate/compile/execute` CLI; SQLGlot validation post-generation | https://github.com/ralforion/orionbelt-semantic-layer |
| TableRAG (EMNLP 2025) | 2 | closest published SQL-RAG blueprint; confirms SQL execution beats document chunking for structured warehouses | https://github.com/yxh-y/TableRAG |
| PremSQL `BaseLineAgent` + `AgentServer` | 2 | multi-turn `/query /analyse /plot /followup` on FastAPI — reference if skills beyond pure SQL are added later | https://github.com/premAI-io/premsql |
| Microsoft OptiGuide | 2 | AutoGen writer/safeguard/interpreter nested chat with `DEBUG_PROMPT` self-repair — reference if ever migrating off Pydantic AI | https://github.com/microsoft/OptiGuide |

### NBA domain grounding

| Reference | Tier | What we lift | Repo URL |
| --- | --- | --- | --- |
| `awesome-nba-data` | 2 | curated NBA data-source index underpinning all domain grounding | https://github.com/JovaniPink/awesome-nba-data |
| `swar/nba_api` | 2 | `find_players_by_full_name` (name→id resolution for `find_player`/`find_team` tools over `bridge_player_source_id`/`dim_all_players`); `expected_data` dicts (~80 endpoints = literal catalogue for business models); `playervsplayer.py` head-to-head result sets (Overall, OnOffCourt, ShotArea*, ShotDistance*) | https://github.com/swar/nba_api |

## Implementation Phasing

- **Phase 1 — Catalog schema + reference YAML (sequential, blocking).** Define the business-model YAML schema (after `flights.yml`) and ship ONE end-to-end model (player career) plus its `MCPSemanticModel` bridge. Every downstream step depends on this shape being stable.
- **Phase 2 — Parallel fill (lanes A/B/C share no files).**
  - **Lane A:** remaining catalog YAMLs (player season, team season, games, awards, standings, shots, head-to-head), each mapped from `meta_metric_definition` / `meta_column_lineage` / `meta_known_gap`.
  - **Lane B:** expanded test fixtures — hand-curated JSON plus a filtered `gretelai/synthetic_text_to_sql` seed subset.
  - **Lane C:** frontend clarification-UX fix (so prompts stay answerable after streaming completes). Touches `frontend/src` only; no `chat_server` overlap with Lanes A/B.
- **Phase 3 — Core refactor of `chat_server` (strictly sequential — every step edits `pipeline.py` / `agent.py`).** Order respects dependencies:
  1. `QueryPlan` restructure: replace `template_id` with `answer_mode` / `question_interpretation` / `sql` / `clarification` / `result_contract`. Existing templates become regression fixtures.
  2. Validation gate: add the `sqlglot.optimizer.optimize` semantic pass and the fan/chasm-trap check on top of the existing `validation.py`.
  3. Dry-run + repair loop: DuckDB dry-run, MAC-SQL Refiner dispatch with the DIN-SQL `debuger()` prompt, `MAX_ROUND` cap.
  4. History persistence: parallel model-history file via `ModelMessagesTypeAdapter`; tool-safe trimming via `keep_last_messages_with_tools()`.
  5. Clarification state: Pydantic state object carrying resolutions across turns.
  6. Composer: read `result_contract` (grain, columns, row limit, answer style) and the catalog's caveat metadata.
- **Phase 4 — Test suite (parallel-safe with Phase 3's later steps).** Stand up `evalite` runners and `tau2-bench` DB-hash-replay scoring on the gold SQL set. Promote fixtures from `"regression"` to `"stable"` as each Phase 3 step lands.

Parallel-safe summary: Phase 2 lanes A/B/C are mutually independent. Phase 3 steps 1–6 are strictly sequential (shared files). Phase 4 can start its skeleton and scorers once Phase 3 step 1 lands, and fill in cases as later steps arrive.
