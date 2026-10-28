"""Pure, scoped result evidence for the new optional verification path.

Nothing here executes SQL, establishes database safety or certifies that the
original question was understood. The caller must use the mandatory safety and
readonly execution boundaries first, and must never pass a truncated preview as
a complete result. Agreement of two computations is evidence only for the
explicit comparison, not an independent business-semantics oracle.
"""
from datetime import date, datetime, time
from decimal import Decimal
import math
from typing import Literal

from .general_sql_validation_v3 import validate_general_sql
from .sql_reviewer import postgres_review_facts


class ObservationOverflow(ValueError):
    """A complete query result exceeds the permitted output resource budget."""


def _evidence(status, code, scope, **details):
    return {"status": status, "code": code, "proof_scope": scope,
            "task_correctness": "NOT_PROVEN", **details}


def observe_rows(rows: list[dict], expected_columns: list[str], max_rows=50) -> dict:
    """Observe complete materialized rows; an empty list cannot prove columns.

    `complete` relies on the caller's full-result contract (read max_rows+1 and
    throw on overflow); it does not mean SQL covered the intended population.
    Column sets are compared by name. Their physical order is recorded without
    inventing a user requirement that SELECT columns use a particular order.
    Types are Python result-value types, not inferred PostgreSQL/catalog types.
    """
    if type(max_rows) is not int or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    if not isinstance(rows, list):
        raise TypeError("rows must be a complete concrete list")
    if len(rows) > max_rows:
        raise ObservationOverflow("Complete result exceeds the configured row budget")
    if (not isinstance(expected_columns, list)
            or any(not isinstance(c, str) or not c for c in expected_columns)):
        raise TypeError("expected_columns must be nonempty column-name strings")
    base={"row_count": len(rows), "complete": True,
          "completeness_source": "caller_full_result_contract_not_inferred_from_row_count",
          "population_completeness": "UNKNOWN", "expected_columns": list(expected_columns)}
    if len(set(expected_columns)) != len(expected_columns):
        return {**base, **_evidence("CONTRADICTED", "DUPLICATE_EXPECTED_COLUMNS", "expected_output_column_identity")}
    if not rows:
        return {**base, "columns": [], "types": {}, "null_counts": {},
                **_evidence("UNKNOWN", "EMPTY_RESULT_COLUMNS_UNKNOWN", "observed_empty_result_not_output_schema")}
    if any(not isinstance(row, dict) or any(not isinstance(k, str) for k in row) for row in rows):
        return {**base, **_evidence("CONTRADICTED", "INVALID_RESULT_ROW", "row_mapping_shape")}
    columns=list(rows[0]);names=set(columns)
    if any(set(row) != names for row in rows):
        return {**base, "columns": columns,
                **_evidence("CONTRADICTED", "INCONSISTENT_RESULT_COLUMNS", "row_mapping_shape")}
    types={column: sorted({type(row[column]).__name__ for row in rows if row[column] is not None}) for column in columns}
    nulls={column: sum(row[column] is None for row in rows) for column in columns}
    declared=bool(expected_columns)
    mismatch=declared and names != set(expected_columns)
    return {**base, "columns": columns, "types": types, "null_counts": nulls,
            "column_order_matches_expected": columns == expected_columns if declared else None,
            **_evidence("CONTRADICTED" if mismatch else "PROVEN",
                        "RESULT_COLUMNS_MISMATCH" if mismatch else "RESULT_SHAPE_OBSERVED",
                        "observed_row_count_columns_types_nulls_only",
                        expected_column_check="names_checked" if declared else "not_requested")}


def _atom(value):
    # bool subclasses int in Python; PostgreSQL boolean is not numeric.
    if value is None:return ("null", None)
    if type(value) is bool:return ("boolean", value)
    if isinstance(value, Decimal):
        if not value.is_finite():raise ValueError("Nonfinite numeric comparison unsupported")
        return ("number", value)
    if type(value) is int:return ("number", Decimal(value))
    if type(value) is float:
        if not math.isfinite(value):raise ValueError("Nonfinite numeric comparison unsupported")
        # Exact binary floating value, not a silently rounded decimal display.
        return ("number", Decimal.from_float(value))
    if isinstance(value, str):return ("text", value)
    if isinstance(value, (datetime, date, time)):return (type(value).__name__, value.isoformat())
    if isinstance(value, bytes):return ("bytes", value)
    raise TypeError("Result comparison requires scalar values")


def compare_results(primary_rows: list[dict], check_rows: list[dict],
                    kind: Literal['same_rows','scalar_equals','diagnostic']='same_rows',
                    primary_column=None, check_column=None) -> dict:
    """Exact declared comparison; a diagnostic does not assert expected equality.

    Numeric-looking text is not coerced into numbers. Decimal comparisons do
    not use ambient precision/normalize/rounding. NULL agrees only with NULL.
    Row ordering is significant for same_rows; scalar_equals permits distinct
    output aliases. The caller supplies a justified comparison purpose BEFORE
    interpreting the result and must not equate two unrelated metrics.
    """
    scope="agreement_of_explicit_computations_not_question_semantics"
    if kind not in {'same_rows','scalar_equals','diagnostic'}:raise ValueError("Unknown result comparison")
    if kind=='diagnostic':
        return _evidence("UNKNOWN", "DIAGNOSTIC_REQUIRES_INTERPRETATION", scope, comparison=kind)
    if not isinstance(primary_rows,list) or not isinstance(check_rows,list):
        raise TypeError("Comparisons require complete row lists")
    if any(not isinstance(row,dict) for row in primary_rows+check_rows):
        return _evidence("UNKNOWN", "COMPARISON_SHAPE_UNSUPPORTED", scope, comparison=kind)
    try:
        if kind=='scalar_equals':
            if len(primary_rows)!=1 or len(check_rows)!=1:
                return _evidence("UNKNOWN", "SCALAR_RESULT_REQUIRED", scope, comparison=kind)
            left,right=primary_rows[0],check_rows[0]
            if primary_column is None and len(left)==1:primary_column=next(iter(left))
            if check_column is None and len(right)==1:check_column=next(iter(right))
            if primary_column not in left or check_column not in right:
                return _evidence("UNKNOWN", "SCALAR_COLUMN_UNRESOLVED", scope, comparison=kind)
            equal=_atom(left[primary_column])==_atom(right[check_column])
        else:
            equal=len(primary_rows)==len(check_rows) and all(
                set(left)==set(right) and all(_atom(left[key])==_atom(right[key]) for key in left)
                for left,right in zip(primary_rows,check_rows))
    except (TypeError, ValueError):
        return _evidence("UNKNOWN", "COMPARISON_VALUE_UNSUPPORTED", scope, comparison=kind)
    return _evidence("PROVEN" if equal else "CONTRADICTED",
                     "OBSERVED_RESULTS_EQUAL" if equal else "OBSERVED_RESULTS_DIFFER",
                     scope, comparison=kind, primary_column=primary_column, check_column=check_column,
                     equivalence_of_SQL_or_population="NOT_PROVEN")


def deterministic_sql_evidence(sql: str, schema: dict, temporal=None,
                               aggregate_contract=None, unique_keys=None) -> dict:
    """Adapt old proof limits to evidence, AFTER mandatory safety validation.

    Missing expectations are not an all-history declaration. Unsupported
    lineage/metric syntax yields UNKNOWN and does not itself forbid safe
    execution. Actual detected unsafe operations remain execution-blocking.
    Legacy purity gaps do not override the mandatory NEW safety result: for
    example SQLGlot TimestampTrunc is PostgreSQL's allowlisted DATE_TRUNC, but
    the old business-proof engine does not recognize that internal node name.
    `PROVEN` concerns bounded structure or explicitly supplied constraints;
    it never proves their agreement with the original natural-language task.
    """
    result=validate_general_sql(sql,schema,temporal,
        aggregate_contract=aggregate_contract,unique_keys=unique_keys)
    status={'satisfied':'PROVEN','violated':'CONTRADICTED','unsupported':'UNKNOWN','unsafe':'CONTRADICTED'}[result.status]
    missing_expectation=temporal is None and result.code=='UNREQUESTED_TEMPORAL_FILTER'
    if missing_expectation:status='UNKNOWN'
    unsafe=result.status=='unsafe'
    legacy_safety_gap=result.code in {'FUNCTION_PURITY_UNPROVEN','CAST_TYPE_UNSUPPORTED'}
    return _evidence(status,result.code,
        "bounded_structure_and_supplied_constraints_not_question_semantics",
        original_status=result.status, original_code=result.code, message=result.message,
        validation=result.model_dump(mode='json'), postgres_facts=postgres_review_facts(sql),
        missing_temporal_expectation=missing_expectation, execution_blocked=unsafe,
        safety_violation=unsafe, safety_purity_unresolved=legacy_safety_gap,
        execution_permission="NOT_GRANTED_BY_EVIDENCE_ADAPTER",
        safety_validation_required_separately=True, sql_rewritten=False)
