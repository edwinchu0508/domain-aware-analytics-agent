"""Real SDK dispatch, synthetic data; no model, PostgreSQL, or answer oracle."""
import json
from types import SimpleNamespace

import pytest
import domain_aware_analytics.analytics_agent_v1 as app
from test_analytics_workflow_v1 import local,intent,call,answer,verdict,ask,ScriptedModel,message


def payload(items):
    return json.loads(items if isinstance(items,str) else items[0]['content'])


CHECK=dict(name='whole_population',sql='SELECT COUNT(oid) AS check_n FROM measurements',
    purpose='Compare the all-observation denominator independently, including missing acceptance',
    comparison='scalar_equals',primary_column='base',check_column='check_n')
BAD='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base FROM measurements WHERE accepted=TRUE'
GOOD='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base FROM measurements'
PLAN=intent(numerator='accepted TRUE observations',denominator='all physical observations',output_columns=['eligible','base'])


def test_repair_reuses_observation_not_contradictory_comparison_and_separates_diagnostics(local):
    diagnostic={**CHECK,'name':'independent_metrics','sql':'SELECT SUM(CASE WHEN accepted=TRUE THEN 1 ELSE 0 END) AS marked,COUNT(oid) AS complete_n FROM measurements','comparison':'diagnostic','primary_column':None,'check_column':None}
    corrected='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base,(SELECT COUNT(oid) FROM measurements) AS diagnostic_n FROM measurements'
    solver=ScriptedModel(lambda items,turn:[call(BAD if turn==1 else corrected,PLAN,turn)] if turn<=2 else [answer('Accepted 2 out of all 4 observations.',result_id=2,rows=[{'eligible':2,'base':4}])])
    def review(items,turn):
        data=payload(items)
        if turn==1:return [verdict('UNKNOWN',queries=[diagnostic,CHECK])]
        assert data['prior_checks'][1]['source_result_id']==1
        assert data['prior_checks'][1]['result_id']==2
        assert data['prior_checks'][1]['original_comparison']['status']=='CONTRADICTED'
        assert data['prior_checks'][1]['comparison']['status']=='PROVEN'
        assert data['prior_checks'][1]['validity']=='VALID'
        assert data['diagnostic_result']==[{'diagnostic_n':4}]
        return [verdict()]
    result,trace=ask(local,solver,ScriptedModel(review),question='Return accepted count and the denominator of ALL observations.')
    assert result['verified'] and result['result']==[{'eligible':2,'base':4}]
    assert trace['repairs_used']==1 and len(trace['SQL_attempts'])==2
    assert len(local[0].executed)==4  # bad, two independent checks, repaired
    assert trace['verification_queries'][1]['comparison']['status']=='CONTRADICTED'
    assert trace['evidence_concerns'][0]['status']=='RESOLVED'
    assert trace['workflow'][-1]['stage']=='completed'
    assert trace['workflow'][-1]['verified']


@pytest.mark.parametrize('damage',['snapshot','missing_result','changed_query','missing_snapshot'])
def test_stale_or_unmatched_old_evidence_cannot_buy_approval(local,monkeypatch,damage):
    original=app._execute_primary
    async def mutate(state,query,plan):
        if state.attempts:
            if damage=='snapshot':local[0].db.execute("INSERT INTO measurements VALUES(5,FALSE,'other',1,'2048-01-01')")
            elif damage=='missing_result':state.native_checks.clear()
            elif damage=='changed_query':state.checks[0]['query']['sql']='SELECT COUNT(*) FROM subjects'
        return await original(state,query,plan)
    monkeypatch.setattr(app,'_execute_primary',mutate)
    if damage=='missing_snapshot':monkeypatch.setattr(local[0],'evidence_context',lambda:None)
    base=5 if damage=='snapshot' else 4
    solver=ScriptedModel(lambda items,turn:[call(BAD if turn==1 else GOOD,PLAN,turn)] if turn<=2 else [answer(result_id=2,rows=[{'eligible':2,'base':base}])])
    review=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',queries=[CHECK])] if turn==1 else [verdict()])
    result,trace=ask(local,solver,review)
    assert not result['verified'] and result['verification_status']=='UNKNOWN'
    latest=trace['verification_queries'][0]['bindings'][-1]
    assert latest['validity']=='STALE' and latest['comparison']['status']=='UNKNOWN'
    assert trace['evidence_concerns'][0]['status']=='OPEN'


def test_new_repair_error_still_contradicts_fresh_independent_evidence(local):
    # Repair changes COUNT expression but retains a wrong population. Approval
    # must NOT override the freshly recomputed 3-vs-4 mismatch.
    wrong='SELECT COUNT(*) FILTER(WHERE accepted=TRUE) AS eligible,COUNT(*) AS base FROM measurements WHERE accepted IS NOT NULL'
    solver=ScriptedModel(lambda items,turn:[call(BAD if turn==1 else wrong,PLAN,turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',queries=[CHECK])] if turn==1 else [verdict()])
    result,trace=ask(local,solver,reviewer)
    assert not result['verified'] and result['status']=='unverified'
    assert trace['SQL_attempts'][1]['rejection']['code']=='INDEPENDENT_QUERY_DISAGREEMENT'
    assert trace['verification_queries'][0]['bindings'][1]['comparison']['status']=='CONTRADICTED'


def test_changed_sealed_population_cannot_reuse_prior_proof(local):
    plans=[PLAN,{**PLAN,'population':'accepted only'}]
    solver=ScriptedModel(lambda items,turn:[call(BAD if turn==1 else GOOD,plans[min(turn-1,1)],turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',queries=[CHECK])])
    result,trace=ask(local,solver,reviewer)
    assert not result['verified'] and trace['SQL_attempts'][1]['rejection']['code']=='INTENT_CHANGED'
    assert trace['evidence_concerns'][0]['status']=='OPEN'
    assert len(local[0].executed)==2


def test_generic_later_approval_cannot_erase_concrete_semantic_contradiction(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements' if turn==1 else 'SELECT COUNT(oid) AS n FROM measurements',intent(),turn)] if turn<=2 else [answer(result_id=2)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('CONTRADICTED',code='QUESTION_SCOPE_CONFLICT')] if turn==1 else [verdict()])
    result,trace=ask(local,solver,reviewer)
    assert not result['verified'] and trace['evidence_concerns'][0]['status']=='OPEN'
    assert trace['SQL_attempts'][0]['rejection']['message']


def test_final_review_resolves_explicit_old_uncertainty_not_workflow_pending(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)] if turn==1 else [answer()])
    def review(items,turn):
        data=payload(items)
        if turn==1:
            assert data['pending_stages']==['final_answer'] and data['review_stage']=='post_execution'
            return [verdict('UNKNOWN',uncertainty=['Check whether every row is included'])]
        assert data['pending_stages']==[] and data['review_stage']=='answer'
        value=json.loads(verdict().content[0].text)
        value['resolved_findings']=[c['id'] for c in data['open_concerns']]
        return [message(json.dumps(value))]
    result,trace=ask(local,solver,ScriptedModel(review))
    assert result['verified'] and not result['uncertainties']
    assert 'pending' not in result['answer'] and 'Unresolved uncertainty' not in result['answer']
    assert trace['evidence_concerns'][0]['status']=='RESOLVED'
    assert trace['repairs_used']==0


def test_diagnostic_cell_cannot_be_claimed_as_final_user_output(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n,99 AS debug_value FROM measurements',intent(),turn)] if turn==1 else [answer(rows=[{'n':4,'debug_value':99}])])
    result,trace=ask(local,solver,ScriptedModel(lambda i,t:[verdict()]),max_repairs=0)
    assert not result['verified'] and result['status']=='unverified'
    assert trace['answer_candidates'][0]['claim_errors']==['Claim references missing successful tool evidence']
    assert trace['SQL_attempts'][0]['diagnostic_rows']==[{'debug_value':99}]


def test_answer_claim_changed_from_execution_is_logged_and_refused(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)] if turn==1 else [answer(rows=[{'n':400}])])
    result,trace=ask(local,solver,ScriptedModel(lambda i,t:[verdict()]),max_repairs=0)
    assert not result['verified'] and result['status']=='unverified'
    assert trace['answer_candidates'][0]['claim_errors']==['Claim value differs from actual returned cell']


def test_missing_required_output_is_not_disguised_as_diagnostic(local):
    solver=ScriptedModel(lambda items,turn:[call('SELECT COUNT(*) AS debug_n FROM measurements',intent(),turn)])
    result,trace=ask(local,solver,ScriptedModel(lambda i,t:pytest.fail('Wrong output must be rejected first')),max_repairs=0)
    assert not result['verified'] and trace['SQL_attempts'][0]['rejection']['code']=='RESULT_COLUMNS_MISMATCH'


def test_session_closed_and_trace_survives_original_sdk_error(local,monkeypatch):
    from agents import UserError
    events=[]
    monkeypatch.setattr(local[0],'begin_evidence_session',lambda:events.append('opened'),raising=False)
    monkeypatch.setattr(local[0],'end_evidence_session',lambda:events.append('closed'),raising=False)
    monkeypatch.setattr(local[0],'execute',lambda *a:(_ for _ in ()).throw(RuntimeError('synthetic failure')))
    solver=ScriptedModel(lambda i,t:[call('SELECT COUNT(*) AS n FROM measurements',intent(),t)])
    with pytest.raises(UserError):ask(local,solver,ScriptedModel(lambda i,t:[]))
    assert events==['opened','closed']
    trace=json.loads((local[1]/'trace.jsonl').read_text())
    assert trace['terminal_status']=='exception' and not trace['response']


def test_cleanup_failure_cannot_erase_trace_or_return_verified(local,monkeypatch):
    def broken():raise RuntimeError('Synthetic cleanup failure')
    monkeypatch.setattr(local[0],'end_evidence_session',broken,raising=False)
    solver=ScriptedModel(lambda i,t:[call('SELECT COUNT(*) AS n FROM measurements',intent(),t)] if t==1 else [answer()])
    with pytest.raises(RuntimeError):ask(local,solver,ScriptedModel(lambda i,t:[verdict()]))
    trace=json.loads((local[1]/'trace.jsonl').read_text())
    assert trace['terminal_status']=='exception' and trace['response'] is None
    assert trace['exception']['stage']=='evidence_session_cleanup'
    assert trace['workflow'][-1]['stage']=='exception'


def test_faithful_claim_cells_do_not_certify_false_answer_prose(local):
    solver=ScriptedModel(lambda i,t:[call('SELECT COUNT(*) AS n FROM measurements',intent(),t)] if t==1 else [answer('There are 400 observations.',rows=[{'n':4}])])
    reviewer=ScriptedModel(lambda i,t:[verdict()] if t==1 else [verdict('CONTRADICTED',code='ANSWER_PROSE_DISAGREES')])
    result,trace=ask(local,solver,reviewer,max_repairs=0)
    assert result['status']=='unverified' and not result['verified']
    assert trace['reviews'][-1]['verdict']['findings'][0]['code']=='ANSWER_PROSE_DISAGREES'
