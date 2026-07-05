"""SQL safety gate for the chatbot's template-driven query layer.

Every SQL string that the runner is allowed to execute MUST first pass
`validate_template_sql(sql, allowed_tables)`. The checks are layered per
PLAN §7.4:

1. Parse with `sqlglot.parse_one(sql, read="duckdb")`; reject on
   `sqlglot.errors.ParseError`.
2. Reject multi-statement input. `sqlglot.parse(sql, read="duckdb")` returns
   one entry per statement — anything beyond one is suspicious.
3. The root expression must be `exp.Select`. Anything else (DDL, DML,
   transaction control) is out.
4. The AST must not contain any of the forbidden node kinds (`Attach`,
   `Copy`, `Create`, `Delete`, `Drop`, `Insert`, `Update`, `Merge`,
   `Alter`, `AlterColumn`, `Pragma`, `Set`, `SetItem`, `Command`).
   `Command` is sqlglot's catch-all for syntax it cannot model directly
   (e.g. `CALL`, `LOAD`, `INSTALL`, `VACUUM`, `CHECKPOINT`, `EXPORT`).
5. Every `exp.Table` referenced must be in the template's
   `allowed_tables` set.

The function returns a `ValidationReport` carrying the boolean verdict,
a list of human-readable errors (empty when valid), and the set of
tables actually referenced (useful for logging the SQL provenance).

Notes for reviewers
-------------------
* The allowlist is enforced *identically* on every render — it is not a
  permission system. A template author who needs a new table extends the
  template's `ALLOWED_TABLES`; there is no runtime escalation path.
* We use `find_all` rather than walking manually because sqlglot's walker
  already handles nested subqueries, CTEs, and joins correctly.
* `parse_one` does *not* raise on multi-statement SQL by default — it
  returns the first statement. We therefore do a second `parse(sql, ...)`
  pass purely to count statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

#: AST node kinds that are never legal inside a chatbot query. The union
#: mirrors PLAN §7.4 step 3 plus `Command` (sqlglot's catch-all for
#: unmodeled statements like CALL / LOAD / INSTALL / VACUUM / CHECKPOINT
#: / EXPORT — many of which lack a dedicated class on `sqlglot.exp`).
_FORBIDDEN: tuple[type[exp.Expression], ...] = (
    exp.Attach,
    exp.Copy,
    exp.Create,
    exp.Delete,
    exp.Drop,
    exp.Insert,
    exp.Update,
    exp.Merge,
    exp.Alter,
    exp.AlterColumn,
    exp.Pragma,
    exp.Set,
    exp.SetItem,
    exp.Command,
)


@dataclass
class ValidationReport:
    """Result of `validate_template_sql`.

    Attributes
    ----------
    valid
        `True` iff `errors` is empty.
    errors
        Human-readable reasons the SQL was rejected. Empty when `valid`.
    tables_referenced
        Distinct base table names referenced by the query, in the order
        sqlglot's walker yielded them. Useful for logging and for the
        template registry's startup self-check.
    """

    valid: bool
    errors: list[str] = field(default_factory=list)
    tables_referenced: set[str] = field(default_factory=set)


def validate_template_sql(sql: str, allowed_tables: set[str]) -> ValidationReport:
    """Check that `sql` is a single, read-only SELECT against the allowlist.

    Parameters
    ----------
    sql
        The rendered SQL to validate. May contain `$name` DuckDB placeholders
        — sqlglot treats them as identifiers and parses them fine without a
        value bound.
    allowed_tables
        The template's `ALLOWED_TABLES` set. Every base table referenced by
        `sql` must be a member; otherwise the query is rejected.

    Returns
    -------
    ValidationReport
        `valid=True` only when the SQL parses, is a single SELECT, contains
        no forbidden node kinds, and references only allowlisted tables.

    Examples
    --------
    >>> r = validate_template_sql("SELECT 1 AS x", set())
    >>> r.valid
    True
    >>> r.tables_referenced
    set()

    >>> r = validate_template_sql("DROP TABLE x", {"mart_player_season"})
    >>> r.valid
    False
    >>> any("DROP" in e for e in r.errors)
    True

    >>> r = validate_template_sql(
    ...     "SELECT * FROM mart_player_season", {"mart_player_season"}
    ... )
    >>> r.valid, r.tables_referenced
    (True, {'mart_player_season'})
    """
    errors: list[str] = []
    tables_referenced: set[str] = set()

    # Step 1: parse.
    try:
        ast = sqlglot.parse_one(sql, read="duckdb")
    except ParseError as exc:
        return ValidationReport(
            valid=False,
            errors=[f"SQL parse error: {exc}"],
        )

    # Step 2: reject multi-statement input. `parse` returns one entry per
    # top-level statement; we only want one.
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except ParseError as exc:
        return ValidationReport(
            valid=False,
            errors=[f"SQL parse error (multi-statement scan): {exc}"],
        )
    if len(statements) != 1:
        return ValidationReport(
            valid=False,
            errors=[f"expected exactly 1 SQL statement, got {len(statements)}"],
        )

    # Step 3: root must be a SELECT.
    if not isinstance(ast, exp.Select):
        return ValidationReport(
            valid=False,
            errors=[f"only SELECT is allowed; got {type(ast).__name__}"],
        )

    # Step 4: forbidden node kinds anywhere in the AST.
    forbidden_hits = [type(node).__name__ for node in ast.find_all(*_FORBIDDEN)]
    if forbidden_hits:
        # Deduplicate + sort for deterministic error messages.
        unique = sorted(set(forbidden_hits))
        errors.append(f"forbidden statements/operations present: {', '.join(unique)}")

    # Step 5: table allowlist.
    #
    # CTE names show up as `exp.Table` nodes in sqlglot's AST even though
    # they aren't real tables — they're local aliases inside the query.
    # Subtract CTE aliases from the table set so the allowlist check only
    # fires on genuine base tables.
    cte_aliases: set[str] = set()
    for cte in ast.find_all(exp.CTE):
        alias = cte.alias
        if alias:
            cte_aliases.add(alias)
    for tbl in ast.find_all(exp.Table):
        name = tbl.name
        # Step 5a: reject catalog/schema-qualified references outright (H1).
        # Our templates never qualify; a qualified name is a defense-in-depth red flag.
        if tbl.db or tbl.catalog:
            errors.append(
                f"catalog/schema-qualified table reference not allowed: {tbl.sql(dialect='duckdb')}"
            )
        if name in cte_aliases:
            continue
        tables_referenced.add(name)
        if name not in allowed_tables:
            errors.append(
                f"table '{name}' is not in the template's allowed set ({sorted(allowed_tables)})"
            )

    # Step 5b: reject dangerous table-valued functions (C2). TVFs like
    # `read_csv_auto`, `read_parquet`, `pragma_*`, `duckdb_*` parse as
    # `exp.Func`/`exp.Anonymous` — NOT `exp.Table` — so the allowlist walk
    # above would miss them, allowing a template to read arbitrary files
    # exposed to the DuckDB process. Deny by name regardless of position.
    _dangerous_tvfs = {
        "read_csv_auto",
        "read_csv",
        "read_parquet",
        "read_json",
        "read_json_auto",
        "read_blob",
        "read_blob_auto",
        "pragma_storage_info",
        "pragma_database_list",
        "pragma_show",
        "duckdb_tables",
        "duckdb_columns",
        "duckdb_indexes",
        "duckdb_constraints",
        "duckdb_schemas",
        "duckdb_views",
        "duckdb_settings",
        "duckdb_databases",
        "duckdb_dependencies",
        "duckdb_functions",
        "duckdb_types",
    }
    for func in ast.find_all(exp.Func):
        try:
            fname = func.sql_name().lower()
        except (AttributeError, ValueError):
            fname = ""
        if fname in _dangerous_tvfs:
            errors.append(f"table-valued function not allowed: {fname}(...)")
    # `read_csv_auto` and similar unmodeled DuckDB functions parse as Anonymous.
    for anon in ast.find_all(exp.Anonymous):
        if anon.name and anon.name.lower() in _dangerous_tvfs:
            errors.append(f"table-valued function not allowed: {anon.name.lower()}(...)")

    return ValidationReport(
        valid=not errors,
        errors=errors,
        tables_referenced=tables_referenced,
    )


__all__ = ["ValidationReport", "validate_template_sql"]
