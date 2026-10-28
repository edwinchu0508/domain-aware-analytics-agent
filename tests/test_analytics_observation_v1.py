"""Scoped evidence only, with no LLM/DB/Gold or changes to the historical V3."""
from decimal import Decimal

import pytest

from domain_aware_analytics.analytics_observation_v1 import (ObservationOverflow,observe_rows,
    compare_results,deterministic_sql_evidence)
from domain_aware_analytics.conditional_aggregation_v3 import AggregatePopulationContract
from domain_aware_analytics.structured_temporal_v2 import TemporalConstraint


def test_observation_records_null_types_names_without_task_certification():
    rows=[{'n':Decimal('3.5'),'label':None},{'n':None,'label':'cedar'}]
    result=observe_rows(rows,['label','n'])
    assert result['status']=='PROVEN' and result['row_count']==2
    assert result['null_counts']=={'n':1,'label':1}
    assert result['types']=={'n':['Decimal'],'label':['str']}
    assert result['column_order_matches_expected'] is False
    assert result['task_correctness']=='NOT_PROVEN' and result['population_completeness']=='UNKNOWN'


def test_empty_column_unknown_and_missing_or_inconsistent_columns_detected():
    assert observe_rows([],['n'])['status']=='UNKNOWN'
    assert observe_rows([{'wrong':1}],['n'])['status']=='CONTRADICTED'
    assert observe_rows([{'n':1},{'other':2}],['n'])['code']=='INCONSISTENT_RESULT_COLUMNS'


def test_overflow_never_becomes_complete_preview():
    with pytest.raises(ObservationOverflow):observe_rows([{'n':1},{'n':2}],['n'],max_rows=1)
    with pytest.raises(ValueError):observe_rows([],[],max_rows=True)


def test_decimal_exact_equality_ignores_scale_without_ambient_rounding():
    long='123456789012345678901234567890123456789'
    assert compare_results([{'n':Decimal(long+'.00')}],[{'n':int(long)}])['status']=='PROVEN'
    assert compare_results([{'n':Decimal(long)}],[{'n':Decimal(long[:-1]+'8')}])['status']=='CONTRADICTED'


@pytest.mark.parametrize('left,right,expected',[(None,None,'PROVEN'),(None,0,'CONTRADICTED'),
    (True,1,'CONTRADICTED'),('001',1,'CONTRADICTED'),(Decimal('0.1'),0.1,'CONTRADICTED')])
def test_null_bool_text_and_float_are_not_silently_coerced(left,right,expected):
    assert compare_results([{'x':left}],[{'x':right}])['status']==expected


def test_row_order_and_names_significant_but_scalar_aliases_can_differ():
    left=[{'k':'p','n':1},{'k':'q','n':2}]
    assert compare_results(left,list(reversed(left)))['status']=='CONTRADICTED'
    assert compare_results([{'a':1}],[{'b':1}])['status']=='CONTRADICTED'
    result=compare_results([{'a':1}],[{'b':Decimal('1.000')}],kind='scalar_equals')
    assert result['status']=='PROVEN' and result['task_correctness']=='NOT_PROVEN'
    assert compare_results(left,[{'n':2}],kind='scalar_equals')['status']=='UNKNOWN'
    assert compare_results(left,left,kind='diagnostic')['status']=='UNKNOWN'


def test_nan_or_unknown_values_are_unknown_not_false_evidence():
    assert compare_results([{'n':Decimal('NaN')}],[{'n':Decimal('NaN')}])['status']=='UNKNOWN'
    assert compare_results([{'n':{'a':1}}],[{'n':{'a':1}}])['status']=='UNKNOWN'


SCHEMA={'measurements':{'mid':'bigint','captured_at':'timestamp without time zone','mass':'numeric','ready':'boolean'}}


def constraint():
    return TemporalConstraint(physical_column='measurements.captured_at',data_type='timestamp without time zone',
        start_inclusive='2046-04-01T00:00:00',end_exclusive='2046-05-01T00:00:00',
        timezone_semantics='naive_calendar',scope='single_select_metric_population')


def test_absent_temporal_expectation_does_not_invent_all_history_conflict():
    result=deterministic_sql_evidence("SELECT COUNT(*) FROM measurements WHERE captured_at >= TIMESTAMP '2046-04-01'",SCHEMA)
    assert result['original_code']=='UNREQUESTED_TEMPORAL_FILTER'
    assert result['status']=='UNKNOWN' and result['missing_temporal_expectation']
    assert not result['execution_blocked']


def test_supplied_time_constraint_proven_or_contradicted_scoped():
    good="SELECT COUNT(*) FROM measurements WHERE captured_at >= TIMESTAMP '2046-04-01' AND captured_at < TIMESTAMP '2046-05-01'"
    result=deterministic_sql_evidence(good,SCHEMA,temporal=constraint())
    assert result['status']=='PROVEN' and result['task_correctness']=='NOT_PROVEN'
    bad=deterministic_sql_evidence(good.replace('2046-05-01','2046-04-30'),SCHEMA,temporal=constraint())
    assert bad['status']=='CONTRADICTED' and not bad['execution_blocked']


def test_missing_conditional_contract_is_unknown_not_inferred_from_sql():
    result=deterministic_sql_evidence("SELECT COUNT(*) FILTER(WHERE ready=TRUE) FROM measurements",SCHEMA)
    assert result['status']=='UNKNOWN'
    assert result['original_code']=='CONDITIONAL_POPULATION_UNSUPPORTED'


def test_supplied_wrong_denominator_contradicted_not_safe_approval():
    contract=AggregatePopulationContract.model_validate(dict(row_source='measurements',outputs=[dict(name='share',
        numerator=dict(function='count',argument='*',condition=[dict(column='measurements.ready',operator='eq',value=True)]),
        denominator=dict(function='count',argument='*'))]))
    query="SELECT COUNT(*) FILTER(WHERE ready=TRUE)::numeric/NULLIF(COUNT(*) FILTER(WHERE ready=TRUE),0) AS share FROM measurements"
    result=deterministic_sql_evidence(query,SCHEMA,aggregate_contract=contract)
    assert result['status']=='CONTRADICTED'
    assert result['original_code']=='CONDITIONAL_METRIC_MISMATCH'


def test_unsupported_multi_branch_and_cte_not_mislabeled_business_error():
    schema={**SCHEMA,'other':{'oid':'bigint','mass':'numeric'}}
    query="WITH a AS (SELECT SUM(mass) AS n FROM measurements),b AS (SELECT SUM(mass) AS n FROM other) SELECT a.n,b.n FROM a CROSS JOIN b"
    result=deterministic_sql_evidence(query,schema)
    assert result['status']=='UNKNOWN' and result['original_code']=='MULTIPLE_POPULATION_BRANCHES'
    assert not result['execution_blocked']


@pytest.mark.parametrize('sql',["DELETE FROM measurements","SELECT pg_sleep(1)","SELECT mid FROM measurements FOR UPDATE"])
def test_actual_unsafe_sql_still_execution_blocked(sql):
    result=deterministic_sql_evidence(sql,SCHEMA)
    assert result['status']=='CONTRADICTED' and result['execution_blocked'] and result['safety_violation']


def test_unknown_legacy_function_purity_not_execution_permission_or_second_gate():
    result=deterministic_sql_evidence('SELECT custom_value(mass) FROM measurements',SCHEMA)
    assert result['status']=='UNKNOWN' and not result['execution_blocked'] and result['safety_purity_unresolved']
    assert result['execution_permission']=='NOT_GRANTED_BY_EVIDENCE_ADAPTER'
    from domain_aware_analytics.analytics_safety_v1 import validate_sql_safety
    assert validate_sql_safety('SELECT custom_value(mass) FROM measurements',SCHEMA).status=='rejected'


def test_new_safety_authoritative_for_proven_postgres_date_trunc_builtin():
    from domain_aware_analytics.analytics_safety_v1 import validate_sql_safety
    sql="SELECT date_trunc('month',captured_at) AS month_start,COUNT(*) AS n FROM measurements GROUP BY date_trunc('month',captured_at)"
    assert validate_sql_safety(sql,SCHEMA).status=='safe'
    result=deterministic_sql_evidence(sql,SCHEMA)
    assert result['original_code']=='FUNCTION_PURITY_UNPROVEN'
    assert result['status']=='UNKNOWN' and not result['execution_blocked']


def test_existing_postgres_sorting_facts_are_evidence_not_approval():
    result=deterministic_sql_evidence('SELECT mid FROM measurements ORDER BY mid DESC',SCHEMA)
    assert result['postgres_facts']['effective_ordering'][0]['effective_nulls']=='first'
    assert result['postgres_facts']['task_correctness']=='not_proven'
