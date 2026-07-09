"""Governed-SQL validation gate (Phase 3 Lane B).

This module LAYERS on top of :func:`chat_server.validation.validate_template_sql`
rather than replacing it. The legacy safety gate (parse + forbidden-node +
allowlist + CTE-alias + TVF + multi-statement checks) stays untouched and
keeps working; this module adds two further checks that the legacy gate does
not perform:

1. **Optimizer semantic pass** -- runs the parsed AST through
   :func:`sqlglot.optimizer.optimize` with a schema derived from the
   :class:`SemanticCatalog`. The optimizer's ``qualify`` / ``annotate_types``
   / ``validator`` sub-passes catch unknown columns, type mismatches, and
   ambiguous references -- a class of bugs (typos in column names,
   stale-column references after a warehouse rebuild) the legacy gate is
   blind to. This is the "free" semantic check sqlglot ships with.

2. **Fan / chasm-trap detection** -- a custom NBA-specific guard against
   one-to-many joins that fan out additive measures. The classic example
   is the ``player_career -> player_season`` join: one career row fans out
   to many per-season rows, and ``SUM(player_season.total_pts)`` without
   a ``GROUP BY`` that collapses on the join key double-counts. This is
   the same class of bug as the exhibition phantom person ids documented
   in ``meta_known_gap.bbr_duplicate_identity_phantom_ids``. The
   detector is deliberately conservative -- it skips ambiguous cases
   rather than false-positive.

The gate's public surface is the two functions:

* :func:`build_catalog_schema` -- derive a sqlglot optimizer schema dict
  from the catalog. Best-effort: column *keys* are required, types are
  not, so the optimizer can do its column-resolution work even when the
  catalog only carries the column name (not the warehouse type).
* :func:`validate_governed_sql` -- the layered entry point used by the
  agent runner.

The ``ValidationReport`` shape is the same one defined in
:mod:`chat_server.validation` -- no new report type is introduced, so
callers can use a single type across both gates.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import sqlglot
from sqlglot import exp, optimizer
from sqlglot.errors import SqlglotError

from chat_server.semantic_catalog.schema import Join as CatalogJoin
from chat_server.validation import ValidationReport, validate_template_sql

if TYPE_CHECKING:
    from chat_server.semantic_catalog import SemanticCatalog


_COL_REF_PATTERN = re.compile(r"\b[A-Za-z_]\w*\s*\.\s*([A-Za-z_]\w*)\b")


_DEFAULT_TYPE = "VARCHAR"


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


def validate_governed_sql(sql: str, catalog: SemanticCatalog) -> ValidationReport:
    """Layered gate: inherited safety + optimizer semantic + fan-trap checks.

    The validation sequence is:

    1. Compute ``allowed_tables`` from ``catalog.models[*].base_table.name``
       and delegate to :func:`validate_template_sql`. If the inherited
       gate rejects the SQL (parse failure, forbidden nodes, table not in
       allowlist, multi-statement, dangerous TVF, etc.), its report is
       returned unchanged -- no further checks are layered on top of a
       known-bad payload.
    2. Re-parse the SQL with :func:`sqlglot.parse_one` and run
       :func:`sqlglot.optimizer.optimize` against the catalog-derived
       schema. Optimizer failures (``OptimizeError`` / ``SchemaError``
       / ``SqlglotError``) are caught and appended as a single
       human-readable error; the verdict is flipped to ``valid=False``.
       This catches unknown columns, type mismatches, and ambiguous
       references -- the legacy gate is blind to all three.
    3. Run :func:`_detect_fan_chasm` over the parsed AST. Any fan-trap
       errors are appended; the verdict is flipped if any are reported.

    Parameters
    ----------
    sql
        The rendered SQL to validate.
    catalog
        The loaded semantic catalog. Both the allowlist (step 1) and the
        optimizer schema (step 2) are derived from it.

    Returns
    -------
    ValidationReport
        Same shape as the legacy report: ``valid`` (True iff no errors
        accumulated), ``errors`` (human-readable reasons), and
        ``tables_referenced`` (the set of base tables the SQL touches --
        inherited from the legacy gate).

    Examples
    --------
    >>> catalog = load_catalog()
    >>> r = validate_governed_sql(
    ...     "SELECT player_id FROM mart_player_career LIMIT 5", catalog
    ... )
    >>> r.valid
    True

    >>> r = validate_governed_sql("SELECT * FROM some_phantom_table", catalog)
    >>> r.valid
    False
    """
    allowed_tables: set[str] = {m.base_table.name for m in catalog.models.values()}

    inherited = validate_template_sql(sql, allowed_tables)
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

    schema = build_catalog_schema(catalog)
    try:
        optimizer.optimize(ast, schema=schema, dialect="duckdb")
    except SqlglotError as exc:
        errors.append(f"sqlglot optimizer rejected the query: {exc}")

    errors.extend(_detect_fan_chasm(ast, catalog))

    return ValidationReport(
        valid=not errors,
        errors=errors,
        tables_referenced=tables_referenced,
    )


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
    errors: list[str] = []

    one_to_many_by_target_model: dict[str, tuple[str, CatalogJoin]] = {}
    model_to_base_table: dict[str, str] = {}
    for src_model in catalog.models.values():
        model_to_base_table[src_model.model] = src_model.base_table.name
        for join_decl in src_model.joins:
            if (
                join_decl.type == "one_to_many"
                and join_decl.model not in one_to_many_by_target_model
            ):
                one_to_many_by_target_model[join_decl.model] = (
                    src_model.model,
                    join_decl,
                )

    base_table_to_model: dict[str, str] = {v: k for k, v in model_to_base_table.items()}

    base_table_names: set[str] = set(model_to_base_table.values())

    for join in ast.find_all(exp.Join):
        joined = join.this
        if not isinstance(joined, exp.Table):
            continue
        joined_table_name = joined.name
        if joined_table_name not in base_table_names:
            continue
        target_model_name = base_table_to_model.get(joined_table_name)
        if target_model_name is None:
            continue
        decl = one_to_many_by_target_model.get(target_model_name)
        if decl is None:
            continue
        src_model_name, join_decl = decl

        joined_aliases: set[str] = set()
        alias_node = joined.alias
        if alias_node:
            alias_str = alias_node.name if hasattr(alias_node, "name") else str(alias_node)
            if alias_str:
                joined_aliases.add(alias_str)
        target_model = catalog.models.get(joined_table_name)
        if target_model is not None:
            joined_aliases.add(target_model.base_table.alias)

        additive_found = False
        for agg in ast.find_all(exp.Sum):
            inner = agg.this
            if not isinstance(inner, exp.Column):
                continue
            if inner.table and inner.table in joined_aliases:
                additive_found = True
                break
        if not additive_found:
            continue

        right_col = _parse_dotted_ref(join_decl.right_on)
        if right_col is None:
            continue
        rt_alias, rt_col = right_col

        group = ast.args.get("group")
        if group is None:
            errors.append(
                f"fan trap: one_to_many join '{join_decl.name}' "
                f"({src_model_name} -> {joined_table_name}) with additive "
                f"SUM measure and no GROUP BY collapsing the fan -- "
                f"re-aggregate in a subquery or use a non-additive path"
            )
            continue

        collapse_found = False
        for g_expr in group.expressions:
            if not isinstance(g_expr, exp.Column):
                continue
            if rt_alias and g_expr.table != rt_alias:
                continue
            if g_expr.name != rt_col:
                continue
            collapse_found = True
            break

        if not collapse_found:
            errors.append(
                f"fan trap: one_to_many join '{join_decl.name}' "
                f"({src_model_name} -> {joined_table_name}) with additive "
                f"SUM measure -- GROUP BY must include {join_decl.right_on!r} "
                f"to collapse the fan"
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


__all__ = ["build_catalog_schema", "validate_governed_sql"]
