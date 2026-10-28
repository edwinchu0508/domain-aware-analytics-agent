"""Actual SDK tool dispatch on synthetic SQLite, with explicitly scripted models.

No paid API calls, PostgreSQL, private benchmark question or measured LLM score.
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from agents import Model, ModelResponse
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText
from domain_aware_analytics.analytics_agent_v1 import AnalyticsConfig, aask_analytics


class ScriptedModel(Model):
    def __init__(self): self.calls = 0
    async def get_response(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            intent = dict(objective='Count all observations', entity_grain='one observation row',
                population='all observations', numerator=None, denominator=None, time_scope='all history',
                required_filters=[], grouping=[], ordering=[], null_empty_policy='COUNT is zero on empty input',
                output_columns=['n'], ambiguities=[])
            output = ResponseFunctionToolCall(type='function_call', name='analytics_run_sql', call_id='demo_count',
                arguments=json.dumps({'query':'SELECT COUNT(*) AS n FROM observations','intent':intent}))
        else:
            answer = dict(answer='There are 3 observations.', uncertainties=[],
                claims=[dict(result_id=1,row=0,column='n',value='3')])
            output = ResponseOutputMessage(id='demo_answer',role='assistant',status='completed',type='message',
                content=[ResponseOutputText(type='output_text',text=json.dumps(answer),annotations=[])])
        return ModelResponse(output=[output],usage=Usage(),response_id=None)
    async def stream_response(self, *args, **kwargs):
        raise AssertionError('Offline demo does not stream')
        yield


class Fixture:
    schema = {'observations':{'observation_id':'integer','reading':'numeric'}}
    def __init__(self):
        self.db=sqlite3.connect(':memory:')
        self.db.row_factory=sqlite3.Row
        self.db.executescript('CREATE TABLE observations(observation_id INTEGER PRIMARY KEY,reading NUMERIC);'
            'INSERT INTO observations VALUES(11,2.5),(12,NULL),(13,7.0);')
    def preflight(self): return {'offline':True,'fixture':'isolated in-memory SQLite only'}
    def unique_keys(self): return {'observations':[['observation_id']]}
    def evidence_context(self): return {'id':'demo-immutable-fixture','basis':'in-memory fixture; no external writers'}
    def execute(self,query,max_rows=50,max_bytes=200000):
        rows=[dict(r) for r in self.db.execute(query).fetchall()]
        assert len(rows)<=max_rows and len(json.dumps(rows).encode())<=max_bytes
        return rows


async def demo():
    db, model = Fixture(), ScriptedModel()
    try:
        result=await aask_analytics('Count all observations.',executor=db,
            config=AnalyticsConfig(variant='A'),generator_model=model,verifier_model=model)
        assert result['status']=='completed' and result['result']==[{'n':3}]
        print('OFFLINE SCRIPTED DEMONSTRATION — no real model/DB requests')
        print(json.dumps(result,indent=2,ensure_ascii=False))
    finally:
        db.db.close()


if __name__=='__main__': asyncio.run(demo())
