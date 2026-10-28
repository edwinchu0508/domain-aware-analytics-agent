"""Phase 2A: prove one naive timestamp interval on a single SELECT population.

This proves temporal SQL-to-contract consistency, not whole-query correctness.
Unsupported syntax is deliberately fail-closed; no SQL is rewritten.
"""
from datetime import datetime, time, timedelta
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, WithJsonSchema, field_validator, model_validator
from sqlglot import exp, parse
from sqlglot.errors import ParseError


# Shared wire-format policy. Calendar validity and ordering remain application
# checks; never coerce an offset-bearing value into a naive timestamp.
NAIVE_DATETIME_PATTERN = (
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?$"
)
NaiveBoundary = Annotated[datetime, WithJsonSchema({
    "type": "string", "pattern": NAIVE_DATETIME_PATTERN,
    "description": (
        "ISO 8601 naive calendar datetime: YYYY-MM-DDTHH:MM:SS with optional "
        "1-6 fractional digits. No Z, timezone offset or timezone conversion. "
        "Must be a valid calendar date; start_inclusive must precede end_exclusive."
    ),
})]


class TemporalConstraint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_column: str = Field(pattern=r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
    data_type: Literal["timestamp without time zone"]
    start_inclusive: NaiveBoundary
    end_exclusive: NaiveBoundary
    timezone_semantics: Literal["naive_calendar"]
    scope: Literal["single_select_metric_population"]

    @field_validator("start_inclusive", "end_exclusive", mode="before")
    @classmethod
    def normalized_boundary(cls, value):
        if isinstance(value, str):
            if not re.fullmatch(NAIVE_DATETIME_PATTERN, value):
                raise ValueError("Use an ISO naive datetime YYYY-MM-DDTHH:MM:SS[.ffffff]")
            value = datetime.fromisoformat(value)
        if not isinstance(value, datetime) or value.tzinfo is not None:
            raise ValueError("Boundary must be a naive datetime; epoch/coerced dates are forbidden")
        return value

    @model_validator(mode="after")
    def ordered(self):
        if self.start_inclusive >= self.end_exclusive:
            raise ValueError("start_inclusive must be earlier than end_exclusive")
        return self


class TemporalValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["satisfied", "violated", "unsupported"]
    code: str
    message: str
    physical_column: str | None = None
    expected: dict = Field(default_factory=dict)
    actual: dict = Field(default_factory=dict)
    predicates: list[str] = Field(default_factory=list)


class _Unsupported(Exception):
    pass


def _unparen(node):
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _literal(node, calendar_date=False):
    node = _unparen(node)
    cast_type = None
    if type(node) is exp.Cast:
        cast_type = node.args["to"].sql(dialect="postgres").upper()
        if cast_type not in ("DATE", "TIMESTAMP"):
            raise _Unsupported("Literal cast requires timezone or precision reasoning")
        node = _unparen(node.this)
    if not isinstance(node, exp.Literal) or not node.is_string:
        raise _Unsupported("Time boundaries must be literal values, not parameters/functions")
    text = node.this
    pattern = r"\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?)?"
    if not re.fullmatch(pattern, text):
        raise _Unsupported("Time literal is not an unambiguous naive ISO date/datetime")
    if (calendar_date or cast_type == "DATE") and len(text) != 10:
        raise _Unsupported("DATE comparisons require date-only literal boundaries")
    if calendar_date and cast_type == "TIMESTAMP":
        raise _Unsupported("Mixed DATE projection and TIMESTAMP literal coercion")
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise _Unsupported("Invalid calendar boundary") from exc


def validate_temporal_sql(sql: str, constraint: TemporalConstraint | None,
                          schema: dict) -> TemporalValidationResult:
    """Normalize effective WHERE/INNER JOIN intervals, with explicit proof limits."""
    expected = constraint.model_dump(mode="json") if constraint is not None else {}
    predicates = []

    def result(status, code, message, actual=None):
        if constraint is not None and status != "satisfied":
            message += (f" Required {constraint.physical_column} >= "
                        f"'{constraint.start_inclusive.isoformat(sep=' ')}' AND "
                        f"{constraint.physical_column} < '{constraint.end_exclusive.isoformat(sep=' ')}'.")
        return TemporalValidationResult(
            status=status, code=code, message=message,
            physical_column=constraint.physical_column if constraint else None,
            expected=expected, actual=actual or {}, predicates=predicates,
        )

    if constraint is None:
        return result("unsupported", "MISSING_TEMPORAL_CONTRACT", "Strict mode requires a typed TemporalConstraint.")
    table_name, column_name = constraint.physical_column.split(".")
    declared = schema.get(table_name, {}).get(column_name)
    if declared != constraint.data_type:
        return result("violated", "CONTRACT_SCHEMA_MISMATCH", "Physical column/type does not match live schema.")

    try:
        trees = [node for node in parse(sql, read="postgres") if node is not None]
        if len(trees) != 1 or not isinstance(trees[0], exp.Select):
            raise _Unsupported("Only one plain SELECT is supported")
        tree = trees[0]
        if tree.args.get("with_") or len(list(tree.find_all(exp.Select))) != 1:
            raise _Unsupported("CTE/subquery lineage is not implemented in Phase 2A")
        if any(tree.find_all(exp.Subquery, exp.Window, exp.Union, exp.Intersect, exp.Except)):
            raise _Unsupported("Subqueries, window functions, and set operations require lineage proofs")
        if any(tree.find_all(exp.Filter, exp.Case)):
            raise _Unsupported("Conditional aggregates/CASE may change the metric population")
        if any(tree.args.get(key) for key in ("having", "qualify", "limit", "offset", "distinct", "into", "locks")):
            raise _Unsupported("Post-aggregation/row-limiting or non-read-only clauses are outside this proof subset")
        source = tree.args.get("from_")
        sources = [source.this] if source else []
        conditions = [tree.args["where"].this] if tree.args.get("where") else []
        for join in tree.args.get("joins", []):
            if (join.args.get("side") or join.args.get("kind") not in (None, "", "INNER")
                    or join.args.get("using") or join.args.get("method") or not join.args.get("on")):
                raise _Unsupported("Only explicit INNER JOIN ON is supported")
            sources.append(join.this)
            conditions.append(join.args["on"])
        aliases = {}
        for source in sources:
            if (not isinstance(source, exp.Table) or not isinstance(source.this, exp.Identifier)
                    or source.db not in ("", "public") or source.catalog
                    or source.name not in schema or source.alias_or_name in aliases):
                raise _Unsupported("Source must resolve unambiguously to a public physical table")
            aliases[source.alias_or_name] = source.name
            alias = source.args.get("alias")
            if (alias is not None and alias.args.get("columns")) or source.args.get("sample"):
                raise _Unsupported("Source column renaming/sampling requires additional population proofs")
        if len(set(aliases.values())) != len(aliases):
            raise _Unsupported("Self-join populations require explicit scope binding")
        if table_name not in aliases.values():
            return result("violated", "WRONG_TEMPORAL_FIELD", "Requested physical table is not in the metric population.")

        def resolve(column):
            if column.catalog or column.db:
                raise _Unsupported("Schema-qualified column expressions are not supported")
            if column.table:
                table = aliases.get(column.table)
                if table is None or column.name not in schema[table]:
                    raise _Unsupported("Unknown physical column or alias")
            else:
                matches = [t for t in aliases.values() if column.name in schema[t]]
                if len(matches) != 1:
                    raise _Unsupported("Unqualified column is ambiguous or unknown")
                table = matches[0]
            return f"{table}.{column.name}", schema[table][column.name]

        def projection(node):
            node = _unparen(node)
            calendar_date = False
            if isinstance(node, exp.Date):
                # Extra arguments can encode a timezone conversion.
                if any(v for k, v in node.args.items() if k != "this"):
                    raise _Unsupported("DATE conversion contains extra semantics")
                calendar_date, node = True, _unparen(node.this)
            elif type(node) is exp.Cast:
                target = node.args["to"].sql(dialect="postgres").upper()
                if target not in ("DATE", "TIMESTAMP"):
                    raise _Unsupported("Column cast changes timezone/precision semantics")
                calendar_date, node = target == "DATE", _unparen(node.this)
            if not isinstance(node, exp.Column) or resolve(node)[0] != constraint.physical_column:
                raise _Unsupported("Temporal expression is not a direct timestamp or proven DATE projection")
            return calendar_date

        def conjunction(node):
            node = _unparen(node)
            if isinstance(node, exp.And):
                return conjunction(node.left) + conjunction(node.right)
            return [node]

        lower = upper = None  # (datetime, inclusive)
        wrong_field = False

        def bound(kind, value, inclusive):
            nonlocal lower, upper
            candidate = (value, inclusive)
            if kind == "lower":
                if lower is None or value > lower[0] or (value == lower[0] and not inclusive):
                    lower = candidate
            elif upper is None or value < upper[0] or (value == upper[0] and not inclusive):
                upper = candidate

        def compare(operator, value, calendar_date):
            if calendar_date:
                tomorrow = value + timedelta(days=1)
                if operator in ("gte", "gt", "eq"):
                    bound("lower", tomorrow if operator == "gt" else value, True)
                if operator in ("lt", "lte", "eq"):
                    bound("upper", value if operator == "lt" else tomorrow, False)
            else:
                if operator in ("gt", "lte") and value.time() != time.min:
                    raise _Unsupported("Last-instant timestamp bounds require physical precision metadata")
                if operator in ("gte", "gt", "eq"):
                    bound("lower", value, operator != "gt")
                if operator in ("lt", "lte", "eq"):
                    bound("upper", value, operator != "lt")

        for condition in conditions:
            if any(condition.find_all(exp.Or, exp.Not)):
                raise _Unsupported("OR/NOT population logic is not implemented")
            for predicate in conjunction(condition):
                refs = [resolve(c) for c in predicate.find_all(exp.Column)]
                temporal_refs = [(name, dtype) for name, dtype in refs
                                 if "timestamp" in dtype.lower() or dtype.lower() == "date"]
                if not temporal_refs:
                    continue
                predicates.append(predicate.sql(dialect="postgres"))
                if any(name != constraint.physical_column for name, _ in temporal_refs):
                    wrong_field = True
                    continue
                if isinstance(predicate, exp.Between):
                    if predicate.args.get("symmetric"):
                        raise _Unsupported("BETWEEN SYMMETRIC is not supported")
                    is_date = projection(predicate.this)
                    compare("gte", _literal(predicate.args["low"], is_date), is_date)
                    compare("lte", _literal(predicate.args["high"], is_date), is_date)
                elif type(predicate) in (exp.GTE, exp.GT, exp.LT, exp.LTE, exp.EQ):
                    left, right = predicate.left, predicate.right
                    op = {exp.GTE: "gte", exp.GT: "gt", exp.LT: "lt", exp.LTE: "lte", exp.EQ: "eq"}[type(predicate)]
                    if not list(left.find_all(exp.Column)):
                        left, right = right, left
                        op = {"gte": "lte", "gt": "lt", "lt": "gt", "lte": "gte", "eq": "eq"}[op]
                    is_date = projection(left)
                    compare(op, _literal(right, is_date), is_date)
                else:
                    raise _Unsupported("Temporal predicate is not a supported comparison")

        actual = {
            "lower": lower[0].isoformat() if lower else None,
            "lower_inclusive": lower[1] if lower else None,
            "upper": upper[0].isoformat() if upper else None,
            "upper_inclusive": upper[1] if upper else None,
        }
        if wrong_field:
            return result("violated", "WRONG_TEMPORAL_FIELD", "WHERE/JOIN filters another physical date field.", actual)
        if lower is None or upper is None:
            return result("violated", "MISSING_TEMPORAL_BOUND", "Effective WHERE/JOIN interval is missing a lower or upper bound.", actual)
        if lower != (constraint.start_inclusive, True) or upper != (constraint.end_exclusive, False):
            return result("violated", "TEMPORAL_RANGE_MISMATCH", "Effective interval or inclusivity differs from the contract.", actual)
        return result("satisfied", "TEMPORAL_RANGE_SATISFIED", "Effective single-SELECT interval equals the typed contract.", actual)
    except _Unsupported as exc:
        return result("unsupported", "UNSUPPORTED_TEMPORAL_EXPRESSION", str(exc))
    except (ValueError, OverflowError, ParseError) as exc:
        return result("unsupported", "UNSUPPORTED_TEMPORAL_EXPRESSION", f"Cannot normalize temporal expression: {type(exc).__name__}")
