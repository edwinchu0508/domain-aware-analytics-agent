"""New safety-only boundary: no live database/model or business-answer oracle."""
import pytest

from domain_aware_analytics.analytics_safety_v1 import validate_sql_safety


SCHEMA = {
    'readings': {'rid': 'bigint', 'at': 'timestamp without time zone', 'val': 'numeric', 'accepted': 'boolean'},
    'repairs': {'repair_id': 'bigint', 'cost': 'numeric', 'reading_id': 'bigint'},
}


@pytest.mark.parametrize('query', [
    'SELECT rid, val FROM readings ORDER BY rid LIMIT 5',
    "SELECT date_trunc('month',at)::date AS month_start,SUM(val) FROM readings GROUP BY date_trunc('month',at)::date",
    'SELECT SUM(CASE WHEN accepted=TRUE THEN val ELSE 0::numeric END) FROM readings',
    'WITH a AS(SELECT SUM(val) AS v FROM readings),b AS(SELECT SUM(cost) AS c FROM repairs) SELECT v,c FROM a CROSS JOIN b',
    'SELECT r.rid,COUNT(p.repair_id) FROM readings r LEFT JOIN repairs p ON p.reading_id=r.rid GROUP BY r.rid',
    'SELECT rid,ROW_NUMBER() OVER(ORDER BY val DESC) AS rank FROM readings',
    'SELECT a.rid FROM readings a WHERE EXISTS(SELECT 1 FROM repairs b WHERE b.reading_id=a.rid)',
    'SELECT rid FROM readings UNION SELECT reading_id FROM repairs',
    'SELECT 1 AS constant',
])
def test_safe_queries_are_not_rejected_for_missing_business_proof(query):
    r = validate_sql_safety(query, SCHEMA)
    assert r.status == 'safe', r.model_dump()
    assert r.business_semantics == 'UNKNOWN'
    assert r.normalized_sql is not None


@pytest.mark.parametrize('query', [
    'DELETE FROM readings',
    'WITH x AS (DELETE FROM readings RETURNING *) SELECT * FROM x',
    'SELECT * INTO new_copy FROM readings',
    'SELECT * FROM readings FOR UPDATE',
    'SELECT * FROM readings; DROP TABLE repairs',
    'SELECT pg_sleep(1)',
    "SELECT set_config('x','y',false)",
    "SELECT pg_read_file('/private')",
    'SELECT public.sum(val) FROM readings',
    'SELECT sneaky_builtin(val) FROM readings',
    'SELECT * FROM pg_catalog.pg_authid',
    'SELECT * FROM private.readings',
    'SELECT * FROM other.public.readings',
    'SELECT * FROM absent',
    'SELECT absent FROM readings',
    'SELECT val FROM readings a JOIN readings b ON a.rid=b.rid',
    'SELECT a.rid FROM readings a WHERE EXISTS(SELECT 1 FROM repairs b WHERE b.reading_id=a.absent)',
    'SELECT a.rid FROM readings a WHERE EXISTS(SELECT 1 FROM repairs b WHERE b.reading_id=x.absent)',
    'SELECT * FROM generate_series(1,1000)',
    'SELECT * FROM readings TABLESAMPLE SYSTEM(1)',
    'SELECT val OPERATOR(public.+) val FROM readings',
    'SELECT val::public.custom_type FROM readings',
    'SELECT val::regclass FROM readings',
    'SELECT * FROM readings LIMIT 500',
    'SELECT * FROM readings OFFSET 1',
    'WITH RECURSIVE x AS(SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x',
    'WITH RECURSIVE x AS(SELECT 1 UNION ALL SELECT 1 FROM x) SELECT * FROM x UNION SELECT 2',
    "SELECT 'unterminated",
    'SELECT 1 /* unterminated',
])
def test_unsafe_or_unproven_execution_safety_fails_closed(query):
    assert validate_sql_safety(query, SCHEMA).status == 'rejected'


def test_scope_resolution_does_not_confuse_ctes_with_physical_tables():
    result = validate_sql_safety('WITH q AS(SELECT val FROM readings) SELECT q.val FROM q',SCHEMA)
    assert result.status == 'safe'
    assert result.physical_tables == ['readings']
    assert validate_sql_safety('WITH q AS(SELECT missing FROM absent) SELECT 1',SCHEMA).status == 'rejected'


def test_unknown_data_types_do_not_assume_pure_overloaded_operators():
    assert validate_sql_safety('SELECT val FROM readings', {'readings': {'val':'USER-DEFINED'}}).status == 'rejected'


def test_no_silent_population_limit_rewrite():
    r = validate_sql_safety('SELECT SUM(val) AS total FROM readings',SCHEMA)
    assert r.status == 'safe' and 'LIMIT' not in r.normalized_sql
    assert validate_sql_safety('SELECT val FROM readings',SCHEMA,max_output_rows=500).status == 'rejected'
