"""Negative end-to-end cases and metered transport boundaries; no live services."""
import asyncio
import json

import pytest
from agents import ModelProvider,ModelResponse,MaxTurnsExceeded
from agents.usage import Usage
from domain_aware_analytics.runtime import BudgetExceeded
import domain_aware_analytics.analytics_agent_v1 as app
from test_analytics_workflow_v1 import local,intent,call,answer,verdict,ask,ScriptedModel


def test_wrong_time_endpoint_observed_then_repaired(local):
    plan=intent(objective='Count January measurements',time_scope='2047-01-01 inclusive to 2047-02-01 exclusive')
    bad="SELECT COUNT(*) AS n FROM measurements WHERE occurred_at>='2047-01-01' AND occurred_at<='2047-02-01'"
    good=bad.replace("<='2047-02-01'","<'2047-02-01'")
    check=dict(name='separate_calendar_cohort',sql="SELECT COUNT(oid) AS jan_n FROM measurements WHERE SUBSTR(occurred_at,1,7)='2047-01'",purpose='Verify January-only membership through an independent month extraction',comparison='scalar_equals',primary_column='n',check_column='jan_n')
    generator=ScriptedModel(lambda items,turn:[call(bad if turn==1 else good,plan,turn)] if turn<=2 else [answer('January has2 rows.',result_id=2,rows=[{'n':2}])])
    reviewer=ScriptedModel(lambda items,turn:[verdict('UNKNOWN',queries=[check])] if turn==1 else [verdict()])
    result,trace=ask(local,generator,reviewer,question='Count all January2047 measurements, excluding February1.')
    assert result['verified'] and result['result']==[{'n':2}]
    assert trace['repairs_used']==1 and trace['verification_queries'][0]['comparison']['status']=='CONTRADICTED'


def test_wrong_intent_is_not_authenticated_by_matching_sql(local):
    wrong=intent(entity_grain='distinct category',population='categories rather than all observations')
    generator=ScriptedModel(lambda items,turn:[call('SELECT COUNT(DISTINCT category) AS n FROM measurements',wrong,turn)])
    reviewer=ScriptedModel(lambda items,turn:[verdict('CONTRADICTED',code='QUESTION_GRAIN_MISMATCH')])
    result,trace=ask(local,generator,reviewer,question='Count every measurement row; do not count categories.')
    assert not result['verified'] and result['status']=='unverified'
    assert len(local[0].executed)==1  # repeated SQL cannot buy another approval
    assert trace['reviews'][0]['verdict']['status']=='CONTRADICTED'


def test_late_independent_query_contradiction_cannot_be_erased_by_new_answer(local):
    bad='SELECT COUNT(*) AS n FROM measurements WHERE accepted=TRUE'
    check=dict(name='all_observations',sql='SELECT COUNT(oid) AS all_n FROM measurements',purpose='Original question requires ALL rows, not just TRUE rows',comparison='scalar_equals',primary_column='n',check_column='all_n')
    generator=ScriptedModel(lambda items,turn:[call(bad,intent(),turn)] if turn==1 else [answer('There are2 observations.',rows=[{'n':2}])])
    reviewer=ScriptedModel(lambda items,turn:[verdict()] if turn==1 else [verdict('UNKNOWN',queries=[check])])
    result,trace=ask(local,generator,reviewer)
    assert not result['verified'] and result['status']=='unverified'
    assert trace['SQL_attempts'][0]['deterministic_contradiction']
    assert len(reviewer.calls)==2 and len(local[0].executed)==2


class KnownUsageModel(ScriptedModel):
    async def get_response(self,*a,**kw):
        response=await super().get_response(*a,**kw)
        return ModelResponse(output=response.output,usage=Usage(requests=1,input_tokens=2,output_tokens=1,total_tokens=3),response_id=None)


class Provider(ModelProvider):
    def __init__(self,model):self.model=model
    def get_model(self,name):return self.model


@pytest.mark.parametrize('known',[True,False])
def test_request_cap_and_unknown_usage_stop_without_fake_zero_cost(local,known):
    db,path,knowledge=local
    klass=KnownUsageModel if known else ScriptedModel
    model=klass(lambda items,turn:[call('SELECT COUNT(*) AS n FROM measurements',intent(),turn)])
    tracepath=path/'metered.jsonl'
    with pytest.raises((BudgetExceeded,MaxTurnsExceeded)):
        asyncio.run(app.aask_analytics('Count every measurements row.',executor=db,
            config=app.AnalyticsConfig(variant='A',max_model_requests=1),trace_path=tracepath,model_provider=Provider(model)))
    trace=json.loads(tracepath.read_text())
    assert trace['terminal_status']=='exception' and len(model.calls)==1
    assert trace['requests']==1
    assert trace['tokens']==(3 if known else None)
    assert trace['usage_known']==known
    request_events=[event for event in trace['events'] if event.get('event') in {'request_started','request_finished'}]
    assert request_events and all(event.get('model')=='gpt-6.1-sol' for event in request_events)
