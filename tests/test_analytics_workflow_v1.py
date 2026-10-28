"""Actual SDK Runner/tool workflow; only model responses and data are synthetic.

SQLite evaluates ordinary synthetic SELECTs. Every HTTP/PostgreSQL connection is
forbidden; these tests make no model calls and carry no billing/Gold evidence.
"""
import asyncio
import json
import sqlite3

import pytest
from agents import UserError
from openai.types.responses import ResponseFunctionToolCall

import domain_aware_analytics.analytics_agent_v1 as backend
from scripted_model import ScriptedModel, message


class FakeExecutor:
    schema = {
        'measurements': {'oid':'bigint','accepted':'boolean','category':'text','amount':'numeric','occurred_at':'timestamp without time zone'},
        'subjects': {'sid':'bigint','team':'text'},
        'test_runs': {'run_id':'bigint','subject_fk':'bigint','eligible':'boolean'},
    }

    def __init__(self):
        self.executed=[]
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
          CREATE TABLE measurements(oid INTEGER PRIMARY KEY,accepted BOOLEAN,category TEXT,amount NUMERIC,occurred_at TEXT);
          INSERT INTO measurements VALUES(1,TRUE,'trail',2,'2047-01-01'),(2,FALSE,'trail',NULL,'2047-01-02'),(3,NULL,'range',8,'2047-02-01'),(4,TRUE,'range',NULL,'2047-02-02');
          CREATE TABLE subjects(sid INTEGER PRIMARY KEY,team TEXT);
          INSERT INTO subjects VALUES(10,'north'),(20,'south');
          CREATE TABLE test_runs(run_id INTEGER PRIMARY KEY,subject_fk INTEGER,eligible BOOLEAN);
          INSERT INTO test_runs VALUES(100,10,TRUE),(101,10,TRUE),(102,20,FALSE);
        ''')

    def preflight(self):return {'offline':True,'select_only':True,'timeout':'5s','source':'SQLite synthetic fixture only'}
    def evidence_context(self):
        # Isolated in-memory fixture: any test-side write invalidates this token.
        return {'id':str(id(self.db))+':'+str(self.db.total_changes),'basis':'isolated_sqlite_fixture_no_external_writers'}
    def unique_keys(self):return {'subjects':[['sid']],'test_runs':[['run_id']],'measurements':[['oid']]}

    def execute(self,query,max_rows=50,max_bytes=200000):
        self.executed.append(query)
        rows=[dict(r) for r in self.db.execute(query).fetchall()]
        if len(rows)>max_rows or len(json.dumps(rows).encode())>max_bytes:raise backend.ResultBudgetExceeded('Fixture output budget')
        return rows


@pytest.fixture
def local(tmp_path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('No real network/PostgreSQL allowed')
    import psycopg
    try:import httpx
    except ModuleNotFoundError:import httpx2 as httpx
    monkeypatch.setattr(psycopg,'connect',forbidden)
    monkeypatch.setattr(httpx.Client,'send',forbidden)
    monkeypatch.setattr(httpx.AsyncClient,'send',forbidden)
    db=FakeExecutor()
    knowledge=tmp_path/'synthetic_dictionary.json'
    knowledge.write_text(json.dumps(dict(version='synthetic1',domain='test instruments',curation_note='Synthetic fixture definitions, no answers or SQL',entries=[dict(
        id='measurement_rows',kind='entity',title='Measurement observations',keywords=['measurements'],references=['measurements.oid'],
        content='CURATED_ROW_DEFINITION: every measurements row is one physical observation; missing acceptance is unknown, not TRUE.',
        review_status='source_curated',provenance=dict(source_file='synthetic fixture',source_section='entity definitions',basis='existing_project_definition',note='Test-only reviewed schema description; not a task answer.'))])),encoding='utf-8')
    yield db,tmp_path,knowledge
    db.db.close()


def intent(**changes):
    value=dict(objective='Count all measurement rows',entity_grain='one physical measurement row',population='all measurements history',
        numerator=None,denominator=None,time_scope='all history',required_filters=[],grouping=[],ordering=[],
        null_empty_policy='COUNT includes every row, empty COUNT is zero',output_columns=['n'],ambiguities=[])
    value.update(changes)
    return value


def call(query,plan,turn):
    return ResponseFunctionToolCall(type='function_call',name='analytics_run_sql',call_id=f'synthetic_sql_{turn}',arguments=json.dumps(dict(query=query,intent=plan)))


def answer(text='There are 4 observations.',result_id=1,rows=None,uncertainty=None):
    rows=rows if rows is not None else [{'n':4}]
    claims=[dict(result_id=result_id,row=i,column=k,value=None if v is None else str(v)) for i,r in enumerate(rows) for k,v in r.items()]
    return message(json.dumps(dict(answer=text,uncertainties=uncertainty or [],claims=claims)))


def verdict(status='PROVEN',queries=None,code='SCOPED_SEMANTIC_REVIEW',uncertainty=None):
    return message(json.dumps(dict(status=status,findings=[dict(status=status,code=code,scope='question population/grain/SQL/result',message='Scoped independent check of supplied question and execution evidence',material=status!='PROVEN')],queries=queries or [],uncertainty=uncertainty or [])))


def ask(local,solver,review,*,variant='C',question='Count every measurements row across all history.',name='trace',**config):
    db,path,knowledge=local
    cfg=backend.AnalyticsConfig(variant=variant,knowledge_path=knowledge,**config)
    result=asyncio.run(backend.aask_analytics(question,executor=db,config=cfg,trace_path=path/f'{name}.jsonl',generator_model=solver,verifier_model=review))
    raw=(path/f'{name}.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(raw)==1
    return result,json.loads(raw[0])


def test_four_variants_share_normal_tools_budgets_and_only_requested_features(local):
    db,path,knowledge=local
    settings=[]
    for variant in 'ABCD':
        db.executed.clear()
        solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)] if turn==1 else [answer()])
        reviewer=ScriptedModel(lambda items,turn:[verdict()])
        result,trace=ask(local,solver,reviewer,variant=variant,name='variant_'+variant)
        assert result['status']=='completed' and result['result']==[{'n':4}]
        assert len(db.executed)==1 and len(solver.calls)==2
        assert len(reviewer.calls)==(2 if variant in 'CD' else 0)
        assert ('CURATED_ROW_DEFINITION' in json.dumps(solver.calls[0]))==(variant in 'BD')
        assert result['verified']==(variant in 'CD')
        if variant in 'AB':assert result['verification_scope']=='execution evidence only; independent verification not requested'
        assert trace['features']==backend.VARIANTS[variant]
        settings.append({k:v for k,v in trace['config'].items() if k!='variant'})
    assert all(value==settings[0] for value in settings)


def test_conditional_grouping_null_zero_and_time_population_actual_toolflow(local):
    plan=intent(objective='For January rows count and sum accepted observations by category',population='measurements during January2047',
        numerator='accepted TRUE observations contribute amount; every other row contributes zero',time_scope='>=2047-01-01,<2047-02-01',
        required_filters=['occurred_at >= 2047-01-01','occurred_at < 2047-02-01'],grouping=['category'],ordering=['category ASC NULLS LAST'],
        null_empty_policy='Skip contributed NULL; per-nonmatch zero, no aggregate COALESCE',output_columns=['category','score','eligible'])
    q="SELECT category,SUM(CASE WHEN accepted=TRUE THEN amount ELSE 0 END) AS score,COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible FROM measurements WHERE occurred_at >= '2047-01-01' AND occurred_at < '2047-02-01' GROUP BY category ORDER BY category ASC"
    rows=[{'category':'trail','score':2,'eligible':1}]
    solver=ScriptedModel(lambda items,turn:[call(q,plan,turn)] if turn==1 else [answer('January trail score2 and eligible1.',rows=rows)])
    review=ScriptedModel(lambda items,turn:[verdict()])
    result,trace=ask(local,solver,review,question='For January2047 measurements, sum accepted amounts with nonmatches contributing zero and count accepted rows by category.')
    assert result['status']=='completed' and result['verified'] and result['result']==rows
    assert trace['SQL_attempts'][0]['observation']['complete']
    assert trace['SQL_attempts'][0]['semantic_evidence']['status']=='UNKNOWN'
    assert len(review.calls)==2


@pytest.mark.parametrize('kind',['denominator','fanout'])
def test_independent_actual_query_disagreement_repairs_unchanged_intent(local,kind):
    if kind=='denominator':
        plan=intent(objective='Count accepted and all measurements separately',numerator='accepted TRUE rows',denominator='all measurement rows',output_columns=['eligible','base'])
        bad='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base FROM measurements WHERE accepted=TRUE'
        good='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base FROM measurements'
        check=dict(name='independent_base',sql='SELECT COUNT(oid) AS all_n FROM measurements',purpose='Check original all-row denominator independently',comparison='scalar_equals',primary_column='base',check_column='all_n')
        rows=[{'eligible':2,'base':4}]
        question='Among ALL measurements give accepted TRUE count and the complete row denominator, including FALSE and NULL.'
    else:
        plan=intent(objective='Count distinct eligible subjects',entity_grain='one distinct subject',population='subjects with at least one eligible test run',numerator='distinct subjects with eligible run',output_columns=['n'])
        bad='SELECT COUNT(s.sid) AS n FROM subjects s JOIN test_runs r ON r.subject_fk=s.sid WHERE r.eligible=TRUE'
        good='SELECT COUNT(DISTINCT s.sid) AS n FROM subjects s JOIN test_runs r ON r.subject_fk=s.sid WHERE r.eligible=TRUE'
        check=dict(name='independent_entities',sql='SELECT COUNT(*) AS unique_n FROM subjects s WHERE EXISTS(SELECT 1 FROM test_runs r WHERE r.subject_fk=s.sid AND r.eligible=TRUE)',purpose='Check parent entities via EXISTS rather than duplicate child rows',comparison='scalar_equals',primary_column='n',check_column='unique_n')
        rows=[{'n':1}]
        question='Count distinct subjects that have any eligible test run, each subject once even if several runs qualify.'
    solver=ScriptedModel(lambda items,turn:[call(bad if turn==1 else good,plan,turn)] if turn<=2 else [answer('Counts are based on the complete intended population.',result_id=2,rows=rows)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',queries=[check])] if turn==1 else [verdict()])
    result,trace=ask(local,solver,reviewer,question=question)
    assert result['status']=='completed' and result['verified'] and result['result']==rows
    assert trace['repairs_used']==1 and len(trace['SQL_attempts'])==2
    assert trace['SQL_attempts'][0]['rejection']['code']=='INDEPENDENT_QUERY_DISAGREEMENT'
    assert trace['verification_queries'][0]['comparison']['status']=='CONTRADICTED'
    assert trace['SQL_attempts'][0]['intent_sha256']==trace['SQL_attempts'][1]['intent_sha256']==trace['intent_sha256']
    assert trace['SQL_attempts'][1]['SQL_diff']
    assert len(local[0].executed)==3 and len(reviewer.calls)==3
    assert any('INDEPENDENT_QUERY_DISAGREEMENT' in str(item) for item in solver.calls[1])


def test_wrong_contract_cannot_be_silently_changed_during_repair(local):
    wrong=intent(population='accepted TRUE measurements only')
    original=intent()
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements WHERE accepted=TRUE' if turn==1 else 'SELECT COUNT(*) AS n FROM measurements',wrong if turn==1 else original,turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('CONTRADICTED',code='QUESTION_POPULATION_MISMATCH')])
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='unverified' and not result['verified']
    assert trace['SQL_attempts'][-1]['rejection']['code']=='INTENT_CHANGED'
    assert len(local[0].executed)==1 and len(reviewer.calls)==1
    assert trace['intent']['population']==wrong['population']


def test_unsafe_sql_never_reaches_database_or_verifier(local):
    solver=ScriptedModel(lambda items,turn:[call('DELETE FROM measurements',intent(),turn)])
    reviewer=ScriptedModel(lambda items,turn:pytest.fail('Reviewer must not see unsafe SQL'))
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='unverified' and not result['verified']
    assert trace['SQL_attempts'][0]['safety']['status']=='rejected'
    assert not local[0].executed and not reviewer.calls and len(solver.calls)==1


def test_repeated_sql_stops_without_duplicate_database_or_review_work(local):
    q='SELECT COUNT(*) AS n FROM measurements'
    solver=ScriptedModel(lambda items,turn:[call(q,intent(),turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('CONTRADICTED',code='EXPLICIT_BUSINESS_CONFLICT')])
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='unverified'
    assert trace['SQL_attempts'][-1]['rejection']['code']=='REPEATED_SQL'
    assert len(local[0].executed)==1 and len(reviewer.calls)==1 and len(solver.calls)==2


def test_no_sql_result_does_not_become_verified_answer(local):
    solver=ScriptedModel(lambda items,turn:[message(json.dumps(dict(answer='I cannot establish a value.',uncertainties=['Missing query evidence'],claims=[])))])
    reviewer=ScriptedModel(lambda items,turn:pytest.fail('No SQL means no result to review'))
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='unverified' and not result['verified'] and result['verification_status']=='UNKNOWN'
    assert not trace['SQL_attempts'] and not local[0].executed


def test_scoped_unknown_retains_answer_with_caveat_instead_of_blanket_grounding_gate(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)] if turn==1 else [answer()])
    reviewer=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',uncertainty=['Population naming remains a business assumption'])])
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='completed' and not result['verified']
    assert result['verification_status']=='UNKNOWN' and '4 observations' in result['answer']
    assert 'Unresolved uncertainty:' in result['answer'] and 'Population naming' in result['answer']
    assert result['result']==[{'n':4}]


def test_two_repairs_is_a_real_sdk_stop_not_manual_handler_calls(local):
    queries=['SELECT COUNT(*) AS n FROM measurements','SELECT COUNT(oid) AS n FROM measurements','SELECT COUNT(1) AS n FROM measurements']
    solver=ScriptedModel(lambda items,turn:[call(queries[turn-1],intent(),turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('CONTRADICTED',code='UNRESOLVED_EXPLICIT_CONFLICT')])
    result,trace=ask(local,solver,reviewer)
    assert result['status']=='unverified' and trace['repairs_used']==2
    assert len(trace['SQL_attempts'])==len(local[0].executed)==len(solver.calls)==len(reviewer.calls)==3
    assert trace['SQL_attempts'][-1]['status']=='rejected'


def test_exception_preserves_original_semantics_and_durable_single_jsonl(local,monkeypatch):
    def broken(*a,**kw):raise RuntimeError('Synthetic disconnected fixture')
    monkeypatch.setattr(local[0],'execute',broken)
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)])
    reviewer=ScriptedModel(lambda items,turn:pytest.fail('Execution failed before review'))
    # The production SDK wraps failed tools in UserError and chains the original
    # exception; require that real SDK behavior rather than a fabricated result.
    with pytest.raises(UserError) as error:
        ask(local,solver,reviewer)
    assert isinstance(error.value.__cause__,RuntimeError)
    lines=(local[1]/'trace.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(lines)==1
    trace=json.loads(lines[0])
    assert trace['terminal_status']=='exception' and trace['exception']['exception_type']=='UserError'
    assert trace['response'] is None and trace['SQL_attempts'][0]['status']=='execution_error'
    assert trace['SQL_attempts'][0]['exception_type']=='RuntimeError'
    assert 'Synthetic disconnected fixture' not in lines[0]
