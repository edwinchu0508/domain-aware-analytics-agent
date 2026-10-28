"""Mandatory execution safety, deliberately separate from business proof.

The existing V3 pure-function and cast-type allowlists are reused unchanged.
``safe`` permits bounded read-only execution, never certifies task correctness.
The caller must additionally enforce a SELECT-only role, read-only transaction,
statement timeout and a complete-result row/byte budget. No SQL is auto-limited.
"""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp, parse
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from .general_sql_validation_v3 import _CAST_TYPES, _KNOWN_UNSAFE_FUNCTIONS, _PURE_FUNCTIONS


class SQLSafetyResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: Literal["safe", "rejected"]
    code: str
    message: str
    category: Literal["unsafe", "unproven", "resource"] | None = None
    normalized_sql: str | None = None
    physical_tables: list[str] = Field(default_factory=list)
    business_semantics: Literal["UNKNOWN"] = "UNKNOWN"


class _Refuse(Exception):
    def __init__(self, code, message, category="unsafe"):
        self.code, self.message, self.category = code, message, category


def _deny(code, message, category="unsafe"):
    raise _Refuse(code, message, category)


_BUILTIN_COLUMN_TYPES = frozenset({
    "smallint", "integer", "bigint", "int", "int2", "int4", "int8", "numeric", "decimal",
    "real", "double precision", "boolean", "bool", "text", "character", "character varying",
    "char", "varchar", "date", "timestamp without time zone", "timestamp with time zone",
    "time without time zone", "time with time zone", "interval", "uuid", "json", "jsonb",
})


def validate_sql_safety(sql: str, schema: dict, max_output_rows: int = 50) -> SQLSafetyResult:
    """Validate known objects/pure AST, not population, grouping or JOIN meaning.

    Safe multi-branch CTEs, subqueries, joins, windows and conditional aggregates
    do not need a formal semantic Contract to execute. Unknown function/object
    safety still FAILS CLOSED. Qualification is performed on a COPY and never
    supplies the executable SQL. ``normalized_sql`` is parser serialization of
    the original tree, not a predicate/limit/Contract rewrite.
    """
    physical_tables = set()
    try:
        if not isinstance(sql, str) or not sql.strip():
            _deny("EMPTY_SQL", "A nonempty SELECT is required.")
        if type(max_output_rows) is not int or not 1 <= max_output_rows <= 50:
            _deny("INVALID_ROW_BUDGET", "The existing maximum output-row budget is 50.", "resource")
        if len(sql) > 50000:
            _deny("SQL_SIZE_BUDGET", "SQL exceeds the bounded parse-size budget.", "resource")
        statements = parse(sql, read="postgres")
        # Empty semicolon/comment statements are not another executable command.
        statements = [s for s in statements if s is not None]
        if len(statements) != 1:
            _deny("MULTIPLE_STATEMENTS", "Exactly one read-only query is required.")
        tree = statements[0]
        if not isinstance(tree, (exp.Select, exp.SetOperation)):
            _deny("READ_ONLY_SELECT_REQUIRED", "Only SELECT queries are allowed.")
        nodes = list(tree.walk())
        if len(nodes) > 5000:
            _deny("AST_SIZE_BUDGET", "SQL exceeds the bounded AST-size budget.", "resource")
        if any(isinstance(n, (exp.DML, exp.DDL, exp.Into)) for n in nodes):
            _deny("PROHIBITED_OPERATION", "Writes, DDL and SELECT INTO are forbidden, including inside CTEs.")
        if any(isinstance(n, exp.Operator) for n in nodes):
            _deny("CUSTOM_OPERATOR_FORBIDDEN", "Explicit custom PostgreSQL operators are forbidden.")
        if any(isinstance(n, exp.Collate) for n in nodes):
            _deny("COLLATION_REFERENCE_UNPROVEN", "Explicit collation objects require separate trusted metadata.", "unproven")
        if any(isinstance(n, exp.With) and n.args.get("recursive") for n in nodes):
            _deny("RECURSIVE_QUERY_BUDGET", "Recursive SQL is outside the bounded execution subset.", "resource")
        for select in tree.find_all(exp.Select):
            if select.args.get("locks"):
                _deny("ROW_LOCK_FORBIDDEN", "Locking SELECT statements are forbidden.")
        for node in nodes:
            if isinstance(node, exp.Offset):
                _deny("OFFSET_BUDGET", "OFFSET remains outside the bounded resource policy.", "resource")
            if isinstance(node, exp.Limit):
                count = node.expression
                if (not isinstance(count, exp.Literal) or not count.is_int
                        or not 0 <= int(count.this) <= max_output_rows
                        or node.args.get("limit_options")):
                    _deny("LIMIT_BUDGET", "LIMIT must be a nonnegative literal within the output-row budget.", "resource")
            if isinstance(node, exp.Table):
                if node.catalog or node.db not in ("", "public"):
                    _deny("FOREIGN_SCHEMA", "Only known public physical relations are allowed.")
                if (not isinstance(node.this, exp.Identifier) or node.args.get("sample")
                        or node.args.get("pivots") or node.args.get("hints")):
                    _deny("NON_PHYSICAL_SOURCE", "Dynamic, sampled or transformed table sources are not allowed.")
            if not isinstance(node, exp.Func) or isinstance(node, (exp.Case, exp.If, exp.Exists)):
                continue
            # These are syntax nodes; every nested actual call remains visited.
            if isinstance(node.parent, exp.Dot):
                _deny("FUNCTION_NOT_ALLOWLISTED", "Explicitly qualified function calls are forbidden.")
            if isinstance(node, exp.Anonymous):
                if str(node.name).upper() in _KNOWN_UNSAFE_FUNCTIONS:
                    _deny("FUNCTION_NOT_ALLOWLISTED", "Side-effectful or server-access functions are forbidden.")
                _deny("FUNCTION_PURITY_UNPROVEN", "Unknown function safety is not established.", "unproven")
            # SQLGlot's internal class is TimestampTrunc, while the PostgreSQL
            # renderer emits the already-allowlisted DATE_TRUNC builtin. Do not
            # confuse the parser's cross-dialect name with an unknown PG call.
            function_name = "DATE_TRUNC" if type(node) is exp.TimestampTrunc else node.sql_name()
            if function_name not in _PURE_FUNCTIONS:
                _deny("FUNCTION_PURITY_UNPROVEN", "The function is outside the existing pure built-in allowlist.", "unproven")
            if isinstance(node, exp.Cast):
                dtype = node.args.get("to")
                name = dtype.this.value if dtype is not None and hasattr(dtype.this, "value") else ""
                if name not in _CAST_TYPES:
                    _deny("CAST_TYPE_UNSUPPORTED", "Custom or unapproved cast types are forbidden.", "unproven")

        analyzed = qualify(tree.copy(), dialect="postgres", schema=schema,
                           validate_qualify_columns=True)
        scopes = list(traverse_scope(analyzed))
        if not scopes or scopes[-1].expression is not analyzed:
            _deny("UNRESOLVED_SCOPE", "The parser cannot resolve this query's sources.", "unproven")
        for scope in scopes:
            if not isinstance(scope.expression, (exp.Select, exp.SetOperation)):
                _deny("NONSELECT_SCOPE", "Only physical-table SELECT scopes are supported.", "unproven")
            physical_aliases = {}
            for alias, (_, source) in scope.selected_sources.items():
                if isinstance(source, exp.Table):
                    if source.name not in schema:
                        _deny("UNKNOWN_PHYSICAL_TABLE", "A physical relation is absent from the live schema.")
                    physical_tables.add(source.name)
                    physical_aliases[alias] = source.name
                elif not isinstance(source, Scope):
                    _deny("UNRESOLVED_SOURCE", "A source is not a known physical table or resolved query scope.", "unproven")
            for column in scope.columns:
                table = physical_aliases.get(column.table)
                if table is None:
                    continue  # resolved CTE/output or correlated ancestor column
                dtype = schema[table].get(column.name)
                if dtype is None:
                    _deny("UNKNOWN_COLUMN", "A physical column is absent from the live schema.")
                name = re.sub(r"\([^)]*\)", "", str(dtype)).strip().lower()
                if name not in _BUILTIN_COLUMN_TYPES:
                    _deny("PHYSICAL_TYPE_UNPROVEN", "Custom or unknown physical types cannot use assumed built-in operator semantics.", "unproven")
        return SQLSafetyResult(status="safe", code="READONLY_AST_SAFETY_PASSED",
            message="Known public objects and allowlisted pure AST; task meaning remains UNKNOWN.",
            normalized_sql=tree.sql(dialect="postgres"), physical_tables=sorted(physical_tables))
    except _Refuse as exc:
        return SQLSafetyResult(status="rejected", code=exc.code, message=exc.message,
                               category=exc.category, physical_tables=sorted(physical_tables))
    except (ParseError, TokenError, OptimizeError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
        # Exception messages can contain raw SQL. Return a stable sanitized code.
        return SQLSafetyResult(status="rejected", code="UNRESOLVED_SQL_OR_SCHEMA",
            message="SQL/schema resolution failed; execution is not allowed.", category="unproven")
