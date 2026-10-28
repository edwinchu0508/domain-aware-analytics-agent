"""Publication plumbing only: config and DB guards, without real connections."""
from contextlib import contextmanager
import pytest
from domain_aware_analytics import postgres
from domain_aware_analytics.analytics_agent_v1 import AnalyticsAnswer, AnalysisIntent
from agents.agent_output import AgentOutputSchema


def config(monkeypatch):
    for key,value in dict(DB_HOST='fixture-host',DB_PORT='5432',DB_NAME='fixture_db',
                          DB_USER='fixture_ro',DB_PASSWORD='fixture-only-not-a-secret').items():
        monkeypatch.setenv(key,value)
    return postgres.PostgresBackend()


def test_adapter_requires_explicit_environment_not_legacy_dotenv(monkeypatch):
    monkeypatch.delenv('DB_NAME',raising=False)
    with pytest.raises(ValueError,match='Explicit database environment'):
        postgres.PostgresBackend()


def test_metadata_connection_still_enforces_readonly_and_timeout(monkeypatch):
    calls=[]
    class Cursor:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,sql,params=None): calls.append((sql,params))
        def fetchall(self): return [{'table_name':'observations','column_name':'id','data_type':'integer'}]
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def cursor(self): return Cursor()
    monkeypatch.setattr(postgres.psycopg,'connect',lambda **kwargs:Connection())
    backend=config(monkeypatch)
    assert backend.snapshot_schema()=={'observations':{'id':'integer'}}
    assert calls[0][0]=='SET TRANSACTION READ ONLY'
    assert calls[1][0]=="SET LOCAL statement_timeout = '5s'"
    assert 'information_schema.columns' in calls[2][0]
    assert backend.DB_CONFIG['dbname']=='fixture_db'


def test_installed_structured_schemas_remain_strict():
    for model in (AnalyticsAnswer,AnalysisIntent):
        schema=AgentOutputSchema(model,strict_json_schema=True).json_schema()
        assert schema['additionalProperties'] is False
        assert set(schema['required'])==set(schema['properties'])
