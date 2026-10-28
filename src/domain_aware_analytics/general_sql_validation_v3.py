"""Bounded, business-agnostic SQL scope/dataflow proof; no I/O or rewriting.

``satisfied`` certifies the declared interval and the supported read-only scope
shape, NOT ranking ties, business joins, denominator meaning or SQL correctness
against the natural-language question.  Those remain independent Reviewer work.
The historical single-SELECT Temporal Guard and owned metric recipes are intact.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from .structured_temporal_v2 import TemporalConstraint, validate_temporal_sql
from .conditional_aggregation_v3 import (AggregatePopulationContract,AggregateTerm,PopulationProofError,
                                        prove_conditional_population)


class GeneralSQLValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["satisfied", "violated", "unsupported", "unsafe"]
    code: str
    message: str
    scopes: list[dict] = Field(default_factory=list)
    temporal: dict = Field(default_factory=dict)
    conditional: dict = Field(default_factory=dict)
    normalized_sql: str | None = None


class _Reject(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


# An allowlist of SQLGlot built-in nodes, not user-supplied names. In particular,
# Anonymous/qualified functions cannot disguise pg_sleep/nextval/server I/O.
_PURE_FUNCTIONS = frozenset({
    "SUM", "AVG", "COUNT", "MIN", "MAX", "ABS", "ROUND", "FLOOR", "CEIL",
    "COALESCE", "NULLIF", "CAST", "EXTRACT", "DATE_TRUNC", "DATE", "TRIM",
    "LOWER", "UPPER", "LENGTH", "CHAR_LENGTH", "SUBSTRING", "ROW_NUMBER",
    "RANK", "DENSE_RANK", "GREATEST", "LEAST", "STDDEV", "STDDEV_POP", "AND", "OR",
    "STDDEV_SAMP", "VARIANCE", "VAR_POP", "VAR_SAMP", "POWER", "SQRT",
})
_WINDOW_FUNCTIONS = frozenset({"SUM", "AVG", "COUNT", "MIN", "MAX", "ROW_NUMBER", "RANK", "DENSE_RANK"})
_RANK_FUNCTIONS = frozenset({"ROW_NUMBER", "RANK", "DENSE_RANK"})
_CAST_TYPES = frozenset({"DATE", "TIMESTAMP", "INT", "INTEGER", "BIGINT", "SMALLINT",
                         "DECIMAL", "NUMERIC", "DOUBLE", "FLOAT", "REAL", "TEXT", "VARCHAR", "BOOLEAN"})
_KNOWN_UNSAFE_FUNCTIONS = frozenset({"PG_SLEEP", "PG_READ_FILE", "PG_READ_BINARY_FILE", "PG_WRITE_FILE",
    "PG_LS_DIR", "PG_TERMINATE_BACKEND", "PG_CANCEL_BACKEND", "SET_CONFIG", "NEXTVAL", "SETVAL",
    "DBLINK", "DBLINK_EXEC", "LO_IMPORT", "LO_EXPORT", "PG_ADVISORY_LOCK", "PG_ADVISORY_XACT_LOCK"})


def validation_capabilities(aggregate_contract: AggregatePopulationContract | None = None) -> dict:
    """Describe this proof engine, not a promise that arbitrary SQL is supported.

    Both profiles are visible before planning. Supplying an independently formed
    aggregate declaration selects its narrower proof profile. No query, question,
    schema-specific recipe, database I/O or model call participates here. Lists
    come from the actual allowlists/model; fresh containers cannot mutate them.
    Conformance tests bind advertised structural flags to accepted/rejected ASTs.
    """
    if aggregate_contract is not None and not isinstance(aggregate_contract, AggregatePopulationContract):
        raise TypeError("Capabilities require a typed aggregate declaration or null")
    return {
        "version": "v3_validator_capabilities_1",
        "source": "general_sql_validation_v3.validate_general_sql",
        "selected_profile": "conditional_population" if aggregate_contract is not None else "general_single_population",
        "business_semantics": "UNKNOWN",
        "proof_scope": "structure_and_declared_population_not_question_correctness",
        "sql_equivalence": "not_assumed; every repair needs fresh proof against the unchanged Contract",
        "safety": {
            "read_only_select": True,
            "single_statement": True,
            "physical_schema": "known public relations only; aliases and CTEs are local bindings",
            "pure_function_allowlist": sorted(_PURE_FUNCTIONS),
            "cast_type_allowlist": sorted(_CAST_TYPES),
            "qualified_or_side_effectful_functions": False,
            "dml_ddl_select_into_row_locks_dynamic_sources": False,
            "unknown_function_purity": "unsupported, not automatically safe",
        },
        "limits": {"literal_final_limit_min": 1, "literal_final_limit_max": 50,
                   "intermediate_limit": False, "offset": False,
                   "multirow_limit_requires_order_by": True,
                   "unique_order_and_tie_meaning": "independent_review_required"},
        "profiles": {
            "general_single_population": {
                "single_select": True,
                "single_physical_base_population": True,
                "acyclic_cte_or_subquery_chain": True,
                "derived_scope_inputs_max": 1,
                "derived_scope_joins": False,
                "multiple_population_branches": False,
                "correlated_subqueries": False,
                "recursive_ctes_and_set_operations": False,
                "group_by": "projected nonaggregates must be covered; no functional-dependency inference",
                "distinct": "structural support only; declared/business entity meaning requires review",
                "physical_inner_join_on": "structural support only; cardinality and business join meaning are not proved by this profile",
                "window_function_allowlist": sorted(_WINDOW_FUNCTIONS),
                "ranking_requires_explicit_order": True,
                "named_windows_and_qualify": False,
                "post_base_filters": "only proved direct-column implications or final direct rank selection; never general predicate relocation",
                "post_rank_reaggregation": False,
                "conditional_aggregates": "require typed conditional_population profile; do not infer declaration from SQL",
            },
            "conditional_population": {
                "typed_population_contract_required": True,
                "single_select": True,
                "direct_physical_population": True,
                "cte_or_subquery": False,
                "window_functions": False,
                "having": False,
                "select_distinct": False,
                "multiple_population_branches": False,
                "aggregate_functions": list(get_args(AggregateTerm.model_fields["function"].annotation)),
                "aggregate_inputs": ["direct_physical_column", "COUNT(*)", "COUNT(1)", "SUM_or_AVG_literal_1"],
                "filter": "conjunctions of direct typed literal comparisons and explicit NULL tests",
                "case": "one searched CASE branch inside supported aggregate only",
                "case_null_fallback": True,
                "case_zero_fallback": "SUM only; not equivalent to COUNT/AVG NULL-skipping",
                "group_by": "direct declared physical columns, all projected",
                "grouping_sets_rollup_cube": False,
                "distinct": "COUNT(DISTINCT one direct physical column) only",
                "distinct_entity": "every term must COUNT(DISTINCT declared entity_key)",
                "inner_join": "declared key equality, matched_inner_rows population and trusted nondeferrable target uniqueness; no inferred keys or unmatched-base coverage",
                "join_key_types": ["integer", "decimal", "boolean"],
                "text_collation_float_join_keys": False,
                "ratio": "same COUNT/SUM input and DISTINCT grain; numerator population subset of denominator (denominator predicate atoms included in numerator); explicit NULLIF(denominator,0) and exact numeric division",
                "ratio_scale": [1, 100],
                "round_digits": {"min": 0, "max": 12},
                "aggregate_coalesce": "explicit COALESCE(aggregate,0), with original NULL/empty policy preserved",
                "temporal_scope": "complete independent typed interval and exact declared base filters; no timezone/precision coercion guesses",
                "contract_or_population_rewriting": False,
            },
        },
    }


def _reject(status, code, message):
    raise _Reject(status, code, message)


def _one_row_aggregate(tree):
    """Only an ungrouped, non-window aggregate can justify LIMIT without order."""
    local = list(_scope_local_nodes(tree))
    return (not tree.args.get("group") and not tree.args.get("having")
            and any(isinstance(n, exp.AggFunc) for n in local)
            and not any(isinstance(n, exp.Window) for n in local))


def _scope_local_nodes(tree):
    """Walk a SELECT's own expressions without descending into nested scopes."""
    stack = [tree]
    while stack:
        node = stack.pop()
        yield node
        for child in node.iter_expressions():
            if isinstance(child, (exp.Select, exp.Subquery, exp.CTE)):
                continue
            stack.append(child)


def _proven_final_rank_filter(select, child):
    """Trace one root rank <= K filter to an actual input-window expression."""
    where = select.args.get("where")
    predicate = where.this if where is not None else None
    if not isinstance(predicate, (exp.LTE, exp.EQ)):
        return False
    column, value = predicate.this, predicate.expression
    if (not isinstance(column, exp.Column) or not isinstance(value, exp.Literal)
            or not value.is_int or not 1 <= int(value.this) <= 50):
        return False
    for expression in child.expression.expressions:
        if expression.alias_or_name != column.name:
            continue
        projected = expression.this if isinstance(expression, exp.Alias) else expression
        return (isinstance(projected, exp.Window) and isinstance(projected.this, exp.Func)
                and projected.this.sql_name() in _RANK_FUNCTIONS
                and bool(projected.args.get("order")))
    return False


def _validate_group_projection(select):
    """Small structural grouping check, not PostgreSQL type/FD inference."""
    local = list(_scope_local_nodes(select))
    grouped_aggregates = [n for n in local if isinstance(n, exp.AggFunc)
                          and not isinstance(n.parent, exp.Window)]
    group = select.args.get("group")
    if group is None and not grouped_aggregates:
        return
    groups = group.expressions if group is not None else []
    plain_columns = {g.sql(dialect="postgres") for g in groups if isinstance(g, exp.Column)}
    group_expressions = {g.sql(dialect="postgres") for g in groups}

    def check(node):
        if node.sql(dialect="postgres") in group_expressions:
            return
        if isinstance(node, exp.Filter) and isinstance(node.this, exp.AggFunc):
            # FILTER is evaluated over the aggregate's input rows, not the
            # grouped output. Its columns need not be GROUP BY keys. A separate
            # conditional population proof validates the whole expression.
            return
        # Window aggregates consume the already-grouped rows, whereas ordinary
        # aggregates consume physical rows and shield their own input columns.
        if isinstance(node, exp.AggFunc) and not isinstance(node.parent, exp.Window):
            return
        if isinstance(node, exp.Column) and node.sql(dialect="postgres") not in plain_columns:
            _reject("unsupported", "UNGROUPED_PROJECTION",
                    "A nonaggregate output is not covered by GROUP BY; functional-dependency inference is not supported.")
        if isinstance(node, exp.Star):
            _reject("unsupported", "UNGROUPED_PROJECTION", "Grouped wildcard projection is not proven.")
        for child in node.iter_expressions():
            check(child)

    for expression in select.expressions:
        check(expression.this if isinstance(expression, exp.Alias) else expression)


# This is an implication subset, not predicate pushdown or an optimizer. An
# accepted derived WHERE must already be TRUE on EVERY input row. Only direct
# value-preserving projections carry facts; aggregate/window/computed outputs
# and NULL-generating grouping extensions do not borrow physical-column facts.
_FACT_SCALAR_TYPES = frozenset({
    "smallint", "integer", "bigint", "int", "int2", "int4", "int8",
    "numeric", "decimal", "real", "double precision", "text", "varchar",
    "character varying", "character", "boolean", "date", "timestamp without time zone",
})
_COMPARISONS = {exp.EQ: "eq", exp.NEQ: "neq", exp.GT: "gt", exp.GTE: "gte",
                exp.LT: "lt", exp.LTE: "lte"}
_REVERSED_COMPARISON = {"eq": "eq", "neq": "neq", "gt": "lt", "gte": "lte",
                        "lt": "gt", "lte": "gte"}


@dataclass(frozen=True)
class _ScopeFacts:
    outputs: dict[str, tuple]
    true_atoms: frozenset[tuple]


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _conjuncts(node):
    node = _unparen(node)
    if isinstance(node, exp.And):
        yield from _conjuncts(node.this)
        yield from _conjuncts(node.expression)
    elif node is not None:
        yield node


def _column_origin(node, scope, scope_id, known):
    node = _unparen(node)
    if not isinstance(node, exp.Column):
        return None
    source_pair = scope.selected_sources.get(node.table)
    if source_pair is None:
        return None
    source = source_pair[1]
    if isinstance(source, exp.Table):
        # Scope/alias identity avoids name-only matching across shadowed CTEs.
        return ("column", scope_id, node.table, source.name, node.name)
    if isinstance(source, Scope) and id(source) in known:
        return known[id(source)].outputs.get(node.name)
    return None


def _predicate_key(node, scope, scope_id, known, schema):
    """Canonical atoms over resolved direct scalar columns, no SQL text match.

    Unknown/user-defined/composite types may have different operator/NULL
    semantics. Do not borrow builtin comparison strictness for those types.
    OR, arbitrary NOT, expressions, coercions and range implication are unproven.
    """
    def term(value):
        value = _unparen(value)
        origin = _column_origin(value, scope, scope_id, known)
        if origin is not None:
            dtype = str(schema.get(origin[3], {}).get(origin[4], "")).lower()
            return origin if dtype in _FACT_SCALAR_TYPES else None
        if isinstance(value, exp.Literal):
            return ("literal", value.is_string, value.this)
        return None

    node = _unparen(node)
    negated = isinstance(node, exp.Not)
    test = _unparen(node.this) if negated else node
    if isinstance(test, exp.Is) and isinstance(test.expression, exp.Null):
        # SQLGlot represents IS NOT NULL with Is(negate=True), whereas
        # explicit NOT (x IS NULL) may use Not(Is(...)). Honor both forms.
        negated = negated != bool(test.args.get("negate"))
        column = term(test.this)
        if column and column[0] == "column":
            return ("not_null" if negated else "is_null", column)
    if negated or type(node) not in _COMPARISONS:
        return None
    left, right = term(node.this), term(node.expression)
    if left is None or right is None or all(v[0] != "column" for v in (left, right)):
        return None
    op = _COMPARISONS[type(node)]
    if left[0] == "literal":
        left, right, op = right, left, _REVERSED_COMPARISON[op]
    if op in ("eq", "neq") and right[0] == "column" and right < left:
        left, right = right, left
    return (op, left, right)


def _output_facts(scope, scope_id, known, schema, derived):
    select = scope.expression
    atoms = set().union(*(known[id(s)].true_atoms for s in derived)) if derived else set()
    if not derived:
        conditions = [j.args.get("on") for j in select.args.get("joins", [])]
        if select.args.get("where"):
            conditions.append(select.args["where"].this)
        for condition in conditions:
            for predicate in _conjuncts(condition):
                key = _predicate_key(predicate, scope, scope_id, known, schema)
                if key is not None:
                    atoms.add(key)
                    # A TRUE builtin strict comparison implies non-NULL scalar
                    # operands, including explicit INNER JOIN ON comparisons.
                    if key[0] in _COMPARISONS.values():
                        atoms.update(("not_null", value) for value in key[1:] if value[0] == "column")
    group = select.args.get("group")
    if group is not None and any(v for k, v in group.args.items() if k != "expressions"):
        return _ScopeFacts({}, frozenset())
    keys = {g.sql(dialect="postgres") for g in group.expressions if isinstance(g, exp.Column)} if group else None
    names = Counter(e.alias_or_name for e in select.expressions)
    outputs = {}
    for expression in select.expressions:
        name = expression.alias_or_name
        value = _unparen(expression.this if isinstance(expression, exp.Alias) else expression)
        if names[name] != 1 or (keys is not None and value.sql(dialect="postgres") not in keys):
            continue
        origin = _column_origin(value, scope, scope_id, known)
        if origin is not None:
            outputs[name] = origin
    visible = set(outputs.values())
    atoms = {key for key in atoms if all(value in visible for value in key[1:] if value[0] == "column")}
    return _ScopeFacts(outputs, frozenset(atoms))


def _derived_predicate_proof(scope, scope_id, known, schema, child):
    predicates = list(_conjuncts(scope.expression.args["where"].this))
    facts = known[id(child)].true_atoms
    keys = [_predicate_key(p, scope, scope_id, known, schema) for p in predicates]
    if not keys or any(key is None or key not in facts for key in keys):
        return None
    return {"status": "entailed", "basis": "inherited_true_constraints_on_direct_column_lineage",
            "all_input_rows_preserved": True,
            "predicates": [p.sql(dialect="postgres") for p in predicates]}


def validate_general_sql(sql: str, schema: dict,
                         temporal: TemporalConstraint | None = None, *,
                         aggregate_contract: AggregatePopulationContract | None = None,
                         unique_keys: dict | None = None) -> GeneralSQLValidation:
    """Prove one physical population flowing through a nonbranching SELECT DAG.

    GROUP/aggregate/window expressions do not themselves remove input rows.
    Final ORDER/LIMIT is supported only AFTER those stages. Nonroot truncation,
    derived-scope filters must be entailed by proven input facts (or the existing
    final-rank rule). Other predicates/branches fail closed; no fixed recipe exists.
    Qualification is analysis on a COPY; returned SQL is never that rewrite.
    """
    evidence, temporal_evidence, conditional_evidence, normalized = [], {}, {}, None
    try:
        try:
            statements = [s for s in parse(sql, read="postgres") if s is not None]
        except (ParseError, TypeError, ValueError):
            _reject("violated", "SQL_PARSE_ERROR", "SQL could not be parsed as a single SELECT.")
        if len(statements) != 1:
            _reject("unsafe", "MULTIPLE_STATEMENTS", "Exactly one SELECT statement is required.")
        tree = statements[0]
        if isinstance(tree, (exp.Union, exp.Intersect, exp.Except)):
            _reject("unsupported", "MULTIPLE_POPULATION_BRANCHES", "Set operations require separate population proofs.")
        if not isinstance(tree, exp.Select):
            _reject("unsafe", "READ_ONLY_SELECT_REQUIRED", "Only a SELECT statement is allowed.")
        forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Into)
        if any(isinstance(n, forbidden) for n in tree.walk()):
            _reject("unsafe", "PROHIBITED_OPERATION", "DML, DDL and SELECT INTO are forbidden, including in CTEs.")
        if any(tree.find_all(exp.Operator)):
            _reject("unsafe", "CUSTOM_OPERATOR_FORBIDDEN", "Explicit PostgreSQL OPERATOR syntax may invoke non-pure user functions.")
        conditional = any(tree.find_all(exp.Filter, exp.Case, exp.If)) or aggregate_contract is not None
        for select in tree.find_all(exp.Select):
            if select.args.get("locks"):
                _reject("unsafe", "ROW_LOCK_FORBIDDEN", "Locking SELECT statements are forbidden.")
            with_clause = select.args.get("with_")
            if with_clause is not None and with_clause.args.get("recursive"):
                _reject("unsupported", "RECURSIVE_SCOPE", "Recursive population lineage is not supported.")
        if any(tree.find_all(exp.Union, exp.Intersect, exp.Except)):
            _reject("unsupported", "MULTIPLE_POPULATION_BRANCHES", "Set operations require separate population proofs.")
        for table in tree.find_all(exp.Table):
            if table.catalog or table.db not in ("", "public"):
                _reject("unsafe", "FOREIGN_SCHEMA", "Only explicitly known public physical relations are allowed.")
            if not isinstance(table.this, exp.Identifier) or table.args.get("sample"):
                _reject("unsafe", "NON_PHYSICAL_SOURCE", "Dynamic relations and sampled sources are forbidden.")
        for func in tree.find_all(exp.Func):
            if isinstance(func, (exp.Case, exp.If)):
                # SQL syntax nodes, not a purity exemption for called functions.
                # ALL nested functions are still inspected below. These nodes
                # require a separate supported aggregate-context proof later.
                continue
            if isinstance(func, exp.Anonymous):
                name = str(func.name).upper()
                qualified_call = isinstance(func.parent, exp.Dot)
                if name in _KNOWN_UNSAFE_FUNCTIONS or qualified_call:
                    _reject("unsafe", "FUNCTION_NOT_ALLOWLISTED", "Side-effectful or explicitly qualified functions are forbidden.")
                _reject("unsupported", "FUNCTION_PURITY_UNPROVEN", "Unknown function purity is unproven; only the pure built-in allowlist is supported.")
            if func.sql_name() not in _PURE_FUNCTIONS:
                _reject("unsupported", "FUNCTION_PURITY_UNPROVEN", "This built-in function is outside the bounded pure-function proof subset.")
            if isinstance(func, exp.Cast):
                dtype = func.args.get("to")
                name = dtype.this.value if dtype is not None and hasattr(dtype.this, "value") else ""
                if name not in _CAST_TYPES:
                    _reject("unsupported", "CAST_TYPE_UNSUPPORTED", "Cast type requires unsupported coercion/timezone reasoning.")
        if conditional and aggregate_contract is None:
            _reject("unsupported", "CONDITIONAL_POPULATION_UNSUPPORTED",
                    "Conditional aggregates require a typed population/numerator/denominator declaration; do not derive it from candidate SQL.")
        normalized = tree.sql(dialect="postgres")
        # The parser tree remains unchanged. Qualify catches unknown/ambiguous
        # physical or derived columns, aliases, and correlation on a separate copy.
        try:
            analyzed = qualify(tree.copy(), dialect="postgres", schema=schema,
                               validate_qualify_columns=True)
            scopes = list(traverse_scope(analyzed))
        except (OptimizeError, ParseError, ValueError, TypeError, KeyError):
            _reject("violated", "UNRESOLVED_COLUMN_OR_SOURCE", "Columns/sources cannot be resolved unambiguously against the supplied schema.")
        if not scopes or scopes[-1].expression is not analyzed:
            _reject("unsupported", "SCOPE_GRAPH_UNSUPPORTED", "A single SELECT root could not be proven.")
        root = scopes[-1]
        graph = {}
        base_scopes = []
        lineage = {}
        for number, scope in enumerate(scopes):
            select = scope.expression
            if not isinstance(select, exp.Select) or scope.is_correlated_subquery:
                _reject("unsupported", "CORRELATED_OR_NONSELECT_SCOPE", "Correlated or non-SELECT scopes require additional lineage proofs.")
            if scope.external_columns:
                _reject("unsupported", "EXTERNAL_SCOPE_REFERENCE", "Cross-scope references are not supported.")
            try:
                sources = list(scope.selected_sources.values())
            except OptimizeError:
                _reject("violated", "AMBIGUOUS_SOURCE", "Every source must have an unambiguous alias.")
            physical = [source for _, source in sources if isinstance(source, exp.Table)]
            derived = [source for _, source in sources if isinstance(source, Scope)]
            predicate_proof = {"status": "not_required"}
            if len(physical) + len(derived) != len(sources) or not sources:
                _reject("unsupported", "SCOPE_SOURCE_UNSUPPORTED", "Each scope requires a physical or proven SELECT source.")
            if physical and derived or len(derived) > 1:
                _reject("unsupported", "MULTIPLE_POPULATION_BRANCHES", "Joins between derived populations need separate multi-scope lineage proofs.")
            if physical:
                base_scopes.append(scope)
                if any(t.name not in schema for t in physical):
                    _reject("violated", "UNKNOWN_PHYSICAL_TABLE", "Physical source is absent from the supplied schema.")
                if len({t.name for t in physical}) != len(physical):
                    _reject("unsupported", "SELF_JOIN_SCOPE", "Self-join populations need explicit role lineage.")
                for join in select.args.get("joins", []):
                    if (join.args.get("side") or join.args.get("kind") not in (None, "", "INNER")
                            or join.args.get("using") or join.args.get("method") or not join.args.get("on")):
                        _reject("unsupported", "JOIN_SCOPE_UNSUPPORTED", "Only physical explicit INNER JOIN ON is proven.")
            else:
                if select.args.get("joins"):
                    _reject("unsupported", "MULTIPLE_POPULATION_BRANCHES", "Derived scopes must have exactly one unjoined input.")
                # Only exact direct-column implication can prove a derived
                # filter redundant. No filter is relocated or removed.
                rank_filter = (scope is root and len(derived) == 1
                               and _proven_final_rank_filter(select, derived[0]))
                if rank_filter and any(isinstance(n, (exp.AggFunc, exp.Window))
                                       for n in _scope_local_nodes(select)):
                    _reject("unsupported", "POST_RANK_REAGGREGATION",
                            "Rank filtering must be final output selection, not input truncation before another aggregate/window.")
                if select.args.get("where"):
                    predicate_proof = ({"status": "existing_final_rank_selection"} if rank_filter else
                        _derived_predicate_proof(scope, number, lineage, schema, derived[0]))
                if (select.args.get("having") or select.args.get("qualify")
                        or predicate_proof is None):
                    _reject("unsupported", "POST_BASE_FILTER_LINEAGE", "Derived-scope filters require additional predicate lineage proofs.")
                for node in _scope_local_nodes(select):
                    if isinstance(node, exp.Func) and node.sql_name() in {"DATE", "DATE_TRUNC", "EXTRACT"}:
                        _reject("unsupported", "DERIVED_TIME_TRANSFORM", "Derived-scope time transformations need explicit timestamp lineage.")
                    if isinstance(node, exp.Cast) and node.args["to"].this.value in {"DATE", "TIMESTAMP", "TIMESTAMPTZ"}:
                        _reject("unsupported", "DERIVED_TIME_TRANSFORM", "Derived timestamp casts need explicit lineage.")
            graph[id(scope)] = derived
            if select.args.get("offset"):
                _reject("unsupported", "OFFSET_UNSUPPORTED", "OFFSET changes the result population and is not supported.")
            limit = select.args.get("limit")
            if limit is not None:
                if scope is not root:
                    _reject("unsupported", "PRE_AGGREGATION_LIMIT", "A nonroot LIMIT can truncate the population before downstream calculations.")
                count = limit.expression
                if (not isinstance(count, exp.Literal) or not count.is_int
                        or not 1 <= int(count.this) <= 50 or limit.args.get("limit_options")):
                    _reject("unsupported", "LIMIT_BUDGET_OR_FORM", "Only a literal final LIMIT between 1 and 50 is supported.")
                if select.args.get("order") is None and not _one_row_aggregate(select):
                    _reject("unsupported", "UNORDERED_LIMIT", "A multirow final LIMIT requires explicit ORDER BY; tie meaning remains Reviewer work.")
            windows = [n for n in _scope_local_nodes(select) if isinstance(n, exp.Window)]
            for window in windows:
                function = window.this
                if (not isinstance(function, exp.Func) or function.sql_name() not in _WINDOW_FUNCTIONS
                        or window.args.get("alias")):
                    _reject("unsupported", "WINDOW_FORM_UNSUPPORTED", "Only direct pure aggregate or ranking windows are supported.")
                if function.sql_name() in _RANK_FUNCTIONS and not window.args.get("order"):
                    _reject("unsupported", "UNORDERED_RANKING", "Ranking windows require explicit ORDER BY; tie semantics remain unproven.")
            if select.args.get("windows"):
                _reject("unsupported", "NAMED_WINDOW_LINEAGE", "Named-window inheritance requires further proof.")
            if select.args.get("qualify"):
                _reject("unsupported", "QUALIFY_UNSUPPORTED", "QUALIFY changes post-window population and is not supported.")
            _validate_group_projection(select)
            facts = _output_facts(scope, number, lineage, schema, derived)
            lineage[id(scope)] = facts
            evidence.append({
                "scope_id": number, "kind": "physical_population" if physical else "derived_population",
                "source_aliases": list(scope.selected_sources), "physical_tables": [t.name for t in physical],
                "input_scope_ids": [scopes.index(s) for s in derived],
                "grouped": bool(select.args.get("group")), "window_functions": [w.this.sql_name() for w in windows],
                "ordered": bool(select.args.get("order")), "final_limit": int(limit.expression.this) if limit else None,
                "operation_order": ["source_where", "group_aggregate", "having", "window", "distinct", "order", "final_limit"],
                "tie_semantics": "reviewer_required_not_unique_order_proof",
                "predicate_proof": predicate_proof,
                "column_lineage": {name: {"base_scope_id": origin[1], "source_alias": origin[2],
                    "physical_column": f"{origin[3]}.{origin[4]}"} for name, origin in facts.outputs.items()},
                "non_null_outputs": sorted(name for name, origin in facts.outputs.items()
                                           if ("not_null", origin) in facts.true_atoms),
            })
        if len(base_scopes) != 1:
            _reject("unsupported", "MULTIPLE_POPULATION_BRANCHES", "Exactly one physical base population is supported.")
        reachable, cursor = set(), root
        while True:
            if id(cursor) in reachable:
                _reject("unsupported", "CYCLIC_SCOPE", "Scope graph must be acyclic.")
            reachable.add(id(cursor))
            children = graph[id(cursor)]
            if not children:
                break
            cursor = children[0]
        if reachable != {id(s) for s in scopes}:
            _reject("unsupported", "UNREACHABLE_OR_BRANCH_SCOPE", "Unused CTEs or additional scalar/subquery branches are not proven.")
        base = base_scopes[0].expression
        projection = base.copy()
        for key in ("with_", "group", "having", "order", "limit", "offset", "distinct", "qualify", "windows"):
            projection.set(key, None)
        projection.set("expressions", [exp.Literal.number(1)])
        projection_sql = projection.sql(dialect="postgres")
        if temporal is not None:
            result = validate_temporal_sql(projection_sql, temporal, schema)
            temporal_evidence = {**result.model_dump(mode="json"), "projection_sql": projection_sql,
                                 "proof_scope": "single_physical_population_before_aggregation_window_limit"}
            if result.status != "satisfied":
                _reject(result.status, result.code, result.message)
        else:
            # NULL means explicitly no time restriction, not an invitation to
            # manufacture a convenient interval. Resolve qualified timestamp
            # references against the physical source aliases, not their names.
            physical_aliases = {alias: source.name for alias, (_, source)
                                in base_scopes[0].selected_sources.items()}
            conditions = ([base.args["where"].this] if base.args.get("where") else [])
            if base.args.get("having"):
                conditions.append(base.args["having"].this)
            for join in base.args.get("joins", []):
                condition = join.args.get("on")
                if condition is not None:
                    conditions.append(condition)
            for condition in conditions:
                timestamp_columns = []
                for column in condition.find_all(exp.Column):
                    table = physical_aliases.get(column.table)
                    dtype = str(schema.get(table, {}).get(column.name, "")).lower()
                    if "timestamp" in dtype or dtype in {"date", "time", "time without time zone", "time with time zone"}:
                        timestamp_columns.append(column)
                if timestamp_columns:
                    # A literal join key equality may relate complete histories;
                    # a range/date condition is an unrequested population bound.
                    if not (condition in [j.args.get("on") for j in base.args.get("joins", [])]
                            and type(condition) is exp.EQ
                            and isinstance(condition.this, exp.Column)
                            and isinstance(condition.expression, exp.Column)
                            and condition.this.table != condition.expression.table):
                        _reject("violated", "UNREQUESTED_TEMPORAL_FILTER",
                                "An explicit all-history Contract cannot add a temporal population predicate.")
            temporal_evidence = {"status": "not_requested", "code": "NO_TEMPORAL_INTERVAL_REQUESTED",
                "scope": "explicit_all_history_no_invented_bounds", "projection_sql": projection_sql,
                "message": "No interval was supplied; this is not proof of question-to-SQL population semantics."}
        if conditional:
            conditional_evidence = prove_conditional_population(scopes, schema, aggregate_contract, unique_keys)
        return GeneralSQLValidation(status="satisfied", code="BOUNDED_SCOPE_PROOF_SATISFIED",
            message="Read-only single-population scope flow and the declared interval are proven; business semantics, joins and ranking ties require independent review.",
            scopes=evidence, temporal=temporal_evidence, conditional=conditional_evidence, normalized_sql=normalized)
    except (_Reject, PopulationProofError) as exc:
        return GeneralSQLValidation(status=exc.status, code=exc.code, message=exc.message,
                                    scopes=evidence, temporal=temporal_evidence, conditional=conditional_evidence, normalized_sql=normalized)
    except (OptimizeError, ParseError, ValueError, TypeError, KeyError, AttributeError, IndexError):
        # Never serialize parser/optimizer exception strings: they can reproduce
        # arbitrary candidate text. Unknown syntax is explicitly fail-closed.
        return GeneralSQLValidation(status="unsupported", code="SCOPE_PROOF_UNSUPPORTED",
            message="The bounded scope/dataflow analysis cannot prove this expression.",
            scopes=evidence, temporal=temporal_evidence, normalized_sql=normalized)
