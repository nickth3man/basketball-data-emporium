"""Three-layer governed-SQL validation gate.

``validate_governed_sql`` applies:

1. :func:`validate_select_sql`: parse one read-only SELECT, reject forbidden
   operations and unsafe table-valued functions, and restrict tables to the
   approved live-schema set.
2. A live-schema optimizer pass, which resolves current table and column names
   and catches unknown or ambiguous references.
3. Catalog-scoped fan/chasm detection for one-to-many joins with additive
   measures.

The third layer is deliberately conservative. It adds NBA-specific protection
against
   one-to-many joins that fan out additive measures. The classic example
   is the ``player_career -> player_season`` join: one career row fans out
   to many per-season rows, and ``SUM(player_season.total_pts)`` without
   a ``GROUP BY`` that collapses on the join key double-counts. This is
   the same class of bug as the exhibition phantom person ids documented
   in ``meta_known_gap.bbr_duplicate_identity_phantom_ids``. The
   detector is deliberately conservative -- it skips ambiguous cases
   rather than false-positive.

The public surface includes:

* :func:`validate_governed_sql` -- the layered entry point used by the
  agent runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp, optimizer
from sqlglot.errors import SqlglotError

from chat_server.semantic_catalog.schema import Join as CatalogJoin

from .db import DuckDBSingleton
from .schema_context import ALLOWED_TABLES_FOR_AGENT

if TYPE_CHECKING:
    from chat_server.semantic_catalog import SemanticCatalog


_COL_REF_PATTERN = re.compile(r"\b[A-Za-z_]\w*\s*\.\s*([A-Za-z_]\w*)\b")


_DEFAULT_TYPE = "VARCHAR"
_APPROVED_TABLE_PREFIXES = ("dim_", "fact_", "mart_", "analytics_")
_DANGEROUS_TVFS = {
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
    valid: bool
    errors: list[str] = field(default_factory=list)
    tables_referenced: set[str] = field(default_factory=set)


def _parse_single_select(sql: str) -> tuple[exp.Select | None, list[str]]:
    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except SqlglotError as exc:
        return None, [f"SQL parse error: {exc}"]

    if len(statements) != 1:
        return None, [f"expected exactly 1 SQL statement, got {len(statements)}"]

    statement = statements[0]
    if not isinstance(statement, exp.Select):
        return None, [f"only SELECT is allowed; got {type(statement).__name__}"]
    return statement, []


def _validate_table_references(
    ast: exp.Select,
    allowed_tables: set[str],
) -> tuple[set[str], list[str]]:
    ctes = {cte.alias for cte in ast.find_all(exp.CTE) if cte.alias}
    tables: set[str] = set()
    errors: list[str] = []

    for table in ast.find_all(exp.Table):
        if table.db or table.catalog:
            reference = table.sql(dialect="duckdb")
            errors.append(f"catalog/schema-qualified table reference not allowed: {reference}")
        if table.name in ctes:
            continue
        tables.add(table.name)
        if table.name not in allowed_tables:
            errors.append(f"table '{table.name}' is not allowed by the approved warehouse set")
    return tables, errors


def _dangerous_function_errors(ast: exp.Select) -> list[str]:
    errors: list[str] = []
    for function in ast.find_all(exp.Func):
        try:
            name = function.sql_name().lower()
        except (AttributeError, ValueError):
            name = ""
        if name in _DANGEROUS_TVFS:
            errors.append(f"table-valued function not allowed: {name}(...)")
    return errors


def validate_select_sql(sql: str, allowed_tables: set[str]) -> ValidationReport:
    """Layer 1: accept one read-only SELECT over approved live tables only."""
    ast, errors = _parse_single_select(sql)
    if ast is None:
        return ValidationReport(False, errors)

    forbidden = sorted({type(node).__name__ for node in ast.find_all(*_FORBIDDEN)})
    if forbidden:
        errors.append(f"forbidden statements/operations present: {', '.join(forbidden)}")

    tables, table_errors = _validate_table_references(ast, allowed_tables)
    errors.extend(table_errors)
    if not errors and not tables:
        errors.append(
            "governed SQL must reference at least one warehouse table "
            "(no FROM or FROM-less SELECT); inline -- comments may "
            "truncate the query and lose table references"
        )
    errors.extend(_dangerous_function_errors(ast))
    return ValidationReport(not errors, errors, tables)


async def build_live_schema(db: DuckDBSingleton) -> dict[str, dict[str, str]]:
    """Return a process-local live snapshot of approved main-schema tables.

    A table is approved if its name starts with one of the canonical
    approved prefixes (``dim_`` / ``fact_`` / ``mart_`` / ``analytics_``)
    OR if it is explicitly listed in
    ``schema_context.ALLOWED_TABLES_FOR_AGENT``.  The union ensures that
    source-backed tables behind curated catalog models (e.g.
    ``src_fact_bref_team_season_summary``) pass the gate without broadly
    allowing all ``src_*`` exploration.
    """
    result = await db.execute(
        """SELECT table_name, column_name, data_type FROM information_schema.columns
           WHERE table_schema = 'main' ORDER BY table_name, ordinal_position""",
        limit=100_000,
    )
    schema: dict[str, dict[str, str]] = {}
    for row in result.rows:
        table = str(row["table_name"])
        if table.startswith(_APPROVED_TABLE_PREFIXES) or table in ALLOWED_TABLES_FOR_AGENT:
            schema.setdefault(table, {})[str(row["column_name"])] = str(row["data_type"])
    return schema


def _extract_columns_from_expr(expr: str) -> list[str]:
    """Return the distinct column names referenced in a catalog ``expr``.

    Best-effort: walks the expression string with a regex that matches
    ``alias.column`` patterns. Works for the catalog's idiomatic shapes:

    * ``pc.player_id`` -> ``['player_id']``
    * ``SUM(pc.career_pts)`` -> ``['career_pts']``
    * ``SUM(ps.w + ps.l)`` -> ``['w', 'l']``
    * ``COUNT(DISTINCT pc.player_id)`` -> ``['player_id']``

    The returned list preserves the order of first appearance so error
    messages stay deterministic.
    """
    seen: list[str] = []
    seen_set: set[str] = set()
    for match in _COL_REF_PATTERN.finditer(expr):
        col = match.group(1)
        if col not in seen_set:
            seen.append(col)
            seen_set.add(col)
    return seen


def build_catalog_schema(catalog: SemanticCatalog) -> dict[str, dict[str, str]]:
    """Derive a sqlglot-optimizer schema from the :class:`SemanticCatalog`.

    The result has the shape ``{table_name: {column_name: type_str}}``
    expected by :func:`sqlglot.optimizer.optimize`. One entry per distinct
    ``base_table.name`` across the catalog. Column types are best-effort:
    every column name extracted from the catalog's dimensions and measures
    gets a default ``"VARCHAR"`` type, which is sufficient for the
    optimizer's ``qualify`` / ``validator`` passes to flag unknown columns
    and ambiguous references. The optimizer only needs the column *keys*
    present; types are not strictly required for column resolution.

    Parameters
    ----------
    catalog
        The loaded semantic catalog. ``catalog.models`` is iterated; the
        order of keys in the returned dict follows ``catalog.models``
        insertion order, which is determined by the loader's
        alphabetical file scan.

    Returns
    -------
    dict[str, dict[str, str]]
        Outer key is the model ``base_table.name`` (the real warehouse
        table). Inner keys are the union of every dimension and measure
        column name extracted from the model's ``expr`` strings. Every
        inner value is the default ``"VARCHAR"``.

    Examples
    --------
    >>> schema = build_catalog_schema(load_catalog())
    >>> "mart_player_career" in schema
    True
    >>> "player_id" in schema["mart_player_career"]
    True
    """
    schema: dict[str, dict[str, str]] = {}
    for model in catalog.models.values():
        table_name = model.base_table.name
        cols = schema.setdefault(table_name, {})
        for dim in model.dimensions:
            for col in _extract_columns_from_expr(dim.expr):
                cols.setdefault(col, _DEFAULT_TYPE)
        for measure in model.measures:
            for col in _extract_columns_from_expr(measure.expr):
                cols.setdefault(col, _DEFAULT_TYPE)
    return schema


async def validate_governed_sql(
    sql: str, db: DuckDBSingleton, catalog: SemanticCatalog | None = None
) -> ValidationReport:
    """Apply read-only, live-schema, and catalog fan-trap checks.

    The validation sequence is:

    1. Build the approved live schema and apply :func:`validate_select_sql`.
       Rejected SQL stops here.
    2. Re-parse the SQL and optimize it against that live schema. Optimizer
       failures catch unknown columns, type mismatches, and ambiguity.
    3. When a catalog is available, run :func:`_detect_fan_chasm` over the
       parsed AST and append any catalog-scoped fan-trap errors.

    Parameters
    ----------
    sql
        The rendered SQL to validate.
    catalog
        The optional semantic catalog used by layer 3 fan/chasm detection.

    Returns
    -------
    ValidationReport
        ``valid`` is true iff no layer produced errors. ``tables_referenced``
        contains live base tables read by the query.

    Examples
    --------
    >>> catalog = load_catalog()
    >>> r = await validate_governed_sql(
    ...     "SELECT player_id FROM mart_player_career LIMIT 5", db, catalog
    ... )
    >>> r.valid
    True

    >>> r = await validate_governed_sql("SELECT * FROM some_phantom_table", db, catalog)
    >>> r.valid
    False
    """
    schema = await build_live_schema(db)
    inherited = validate_select_sql(sql, set(schema))
    if not inherited.valid:
        return inherited

    errors: list[str] = list(inherited.errors)
    tables_referenced: set[str] = set(inherited.tables_referenced)

    try:
        ast = sqlglot.parse_one(sql, read="duckdb")
    except SqlglotError as exc:
        errors.append(f"SQL parse error: {exc}")
        return ValidationReport(
            valid=False,
            errors=errors,
            tables_referenced=tables_referenced,
        )

    try:
        optimizer.optimize(ast, schema=schema, dialect="duckdb")
    except SqlglotError as exc:
        errors.append(f"sqlglot optimizer rejected the query: {exc}")

    if catalog is not None:
        errors.extend(_detect_fan_chasm(ast, catalog))

    return ValidationReport(
        valid=not errors,
        errors=errors,
        tables_referenced=tables_referenced,
    )


def _fan_join_indexes(
    catalog: SemanticCatalog,
) -> tuple[dict[str, tuple[str, CatalogJoin]], dict[str, str]]:
    one_to_many_by_target: dict[str, tuple[str, CatalogJoin]] = {}
    model_to_base_table: dict[str, str] = {}

    for source_model in catalog.models.values():
        model_to_base_table[source_model.model] = source_model.base_table.name
        for join in source_model.joins:
            if join.type == "one_to_many" and join.model not in one_to_many_by_target:
                one_to_many_by_target[join.model] = (source_model.model, join)

    base_table_to_model = {table: model for model, table in model_to_base_table.items()}
    return one_to_many_by_target, base_table_to_model


def _joined_aliases(joined: exp.Table, catalog: SemanticCatalog) -> set[str]:
    aliases: set[str] = set()
    if joined.alias:
        aliases.add(str(joined.alias))

    target_model = catalog.models.get(joined.name)
    if target_model is not None:
        aliases.add(target_model.base_table.alias)
    return aliases


def _has_additive_sum(ast: exp.Expression, joined_aliases: set[str]) -> bool:
    return any(
        isinstance(aggregate.this, exp.Column)
        and bool(aggregate.this.table)
        and aggregate.this.table in joined_aliases
        for aggregate in ast.find_all(exp.Sum)
    )


def _group_collapses_fan(group: exp.Group, alias: str, column: str) -> bool:
    return any(
        isinstance(expression, exp.Column)
        and (not alias or expression.table == alias)
        and expression.name == column
        for expression in group.expressions
    )


def _fan_trap_error(
    join: CatalogJoin,
    source_model: str,
    joined_table: str,
    *,
    no_group: bool,
) -> str:
    prefix = (
        f"fan trap: one_to_many join '{join.name}' "
        f"({source_model} -> {joined_table}) with additive SUM measure"
    )
    if no_group:
        return (
            f"{prefix} and no GROUP BY collapsing the fan -- "
            "re-aggregate in a subquery or use a non-additive path"
        )
    return f"{prefix} -- GROUP BY must include {join.right_on!r} to collapse the fan"


def _detect_fan_chasm(ast: exp.Expression, catalog: SemanticCatalog) -> list[str]:
    """Append-only detector for one_to_many joins with additive SUM measures.

    Walks every :class:`exp.Join` in ``ast``. For each join, looks up the
    catalog for a :class:`Join` declaration that targets the joined table
    with ``type == "one_to_many"``. If such a declaration exists:

    * the joined table is the "many" / fanned side,
    * a :class:`exp.Sum` aggregate over a column aliased to the joined
      table is taken as evidence of an additive measure from the fanned
      side,
    * the gate trips unless the AST's ``GROUP BY`` contains a column
      reference that literally matches the catalog's ``right_on`` for
      that join (the column that would collapse the fan).

    Conservatism
    ------------
    The detector is deliberately conservative:

    * Only literal alias matches count as "GROUP BY collapses the fan".
      If the join uses ``ps.player_id`` (catalog alias) but the SQL
      references ``season.player_id`` (a different alias for the same
      table), the detector skips rather than chasing alias rewriting.
      False positives are worse than false negatives for v1.
    * CTEs and subqueries are not unravelled. A CTE that pre-aggregates
      the many side before joining is a safe pattern; the detector will
      still see the outer join and may flag it. This is acceptable for
      v1 -- the workaround (a hand-built subquery with an explicit
      comment) is straightforward.
    * A bare ``SUM`` aggregate is treated as additive regardless of the
      catalog's ``additivity`` field. The catalog's ``sum`` measures all
      use ``SUM(...)`` at their core, so this catches them; the cost is
      that hand-written ``SUM(col)`` from a non-additive measure (e.g.
      ``SUM(ppg)``) would also trip -- but ``SUM(ppg)`` is itself a fan
      trap, so flagging it is correct.

    The detector returns a list of error strings (empty when no traps
    found). It does NOT mutate the AST or the catalog.
    """
    one_to_many_by_target_model, base_table_to_model = _fan_join_indexes(catalog)
    errors: list[str] = []

    for join in ast.find_all(exp.Join):
        joined = join.this
        if not isinstance(joined, exp.Table):
            continue
        joined_table_name = joined.name
        target_model_name = base_table_to_model.get(joined_table_name)
        if target_model_name is None:
            continue
        decl = one_to_many_by_target_model.get(target_model_name)
        if decl is None:
            continue
        src_model_name, join_decl = decl

        if not _has_additive_sum(ast, _joined_aliases(joined, catalog)):
            continue

        right_col = _parse_dotted_ref(join_decl.right_on)
        if right_col is None:
            continue
        rt_alias, rt_col = right_col

        group = ast.args.get("group")
        if not isinstance(group, exp.Group):
            errors.append(
                _fan_trap_error(join_decl, src_model_name, joined_table_name, no_group=True)
            )
            continue

        if not _group_collapses_fan(group, rt_alias, rt_col):
            errors.append(
                _fan_trap_error(join_decl, src_model_name, joined_table_name, no_group=False)
            )

    return errors


def _parse_dotted_ref(ref: str) -> tuple[str, str] | None:
    """Parse ``"alias.column"`` into ``(alias, column)``.

    Returns ``(alias, column)`` for a dotted reference and ``("", ref)``
    for an unprefixed column name. Returns ``None`` for an empty / blank
    string. Whitespace around the dot is tolerated.
    """
    ref = ref.strip()
    if not ref:
        return None
    if "." in ref:
        alias, _, column = ref.partition(".")
        return alias.strip(), column.strip()
    return "", ref


__all__ = [
    "ValidationReport",
    "validate_select_sql",
    "build_live_schema",
    "build_catalog_schema",
    "validate_governed_sql",
]
