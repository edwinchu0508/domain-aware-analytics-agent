"""Offline two-request baseline tests; no private inputs or live services."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from domain_aware_analytics import baseline_e as e
DEPS = e.dependencies()
def response(text, *, usage=True, status="completed", total=30):
    u = SimpleNamespace(input_tokens=10, output_tokens=total - 10, total_tokens=total,
                        input_tokens_details=SimpleNamespace(cached_tokens=2)) if usage else None
    return SimpleNamespace(id="synthetic-response", status=status, usage=u,
                           incomplete_details=None, output=[SimpleNamespace(type="message")], output_text=text)

class Executor:
    schema = {"invoices": {"invoice_key": "integer", "total_cost": "numeric"}}

    def __init__(self, error=None, preflight_error=None):
        self.error, self.preflight_error = error, preflight_error
        self.queries = []
        self.ended = False

    def preflight(self):
        if self.preflight_error:
            raise self.preflight_error
        return {"readonly": "on"}

    def begin_evidence_session(self):
        return {"snapshot": "synthetic", "readonly": "on", "isolation": "repeatable read", "timeout": "5s"}

    def end_evidence_session(self):
        self.ended = True

    def execute(self, query, max_rows, max_bytes):
        self.queries.append(query)
        assert (max_rows, max_bytes) == (50, 200000)
        if self.error:
            raise self.error
        return [{"amount": "41.25"}]

class Wiring(unittest.IsolatedAsyncioTestCase):

    async def attempt(self, responses, executor=None):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        executor = executor or Executor()
        client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(side_effect=responses)))
        record = await e.run_one('What is the total cost?', executor, client, DEPS, Path(self.temp.name))
        trace = json.loads((Path(self.temp.name) / 'agent_trace.json').read_text(encoding='utf-8'))
        return (record, trace, executor, client)

    async def test_success_two_calls_original_sql_and_minimal_context(self):
        sql = 'select SUM( total_cost ) as amount from invoices'
        record, trace, ex, c = await self.attempt([response(json.dumps({'sql': sql})), response('Total cost is41.25.')])
        self.assertEqual(record['status'], 'completed')
        self.assertEqual(record['requests'], 2)
        self.assertEqual(ex.queries, [sql])
        self.assertTrue(ex.ended)
        calls = c.responses.create.call_args_list
        for call in calls:
            self.assertEqual((call.kwargs['model'], call.kwargs['reasoning']), ('gpt-6.1-sol', {'effort': 'medium'}))
            self.assertEqual(call.kwargs['max_output_tokens'], 8192)
            self.assertEqual(call.kwargs['tools'], [])
            self.assertEqual(call.kwargs['tool_choice'], 'none')
            self.assertFalse(call.kwargs['parallel_tool_calls'])
        self.assertEqual(set(json.loads(calls[0].kwargs['input'][0]['content'])), {'question', 'schema'})
        self.assertEqual(set(json.loads(calls[1].kwargs['input'][0]['content'])), {'question', 'schema', 'executed_sql', 'complete_result', 'row_count'})
        self.assertEqual(trace['reviewer_calls'], 0)
        self.assertFalse(record['verified'])

    async def test_bad_json_no_sql_no_retry(self):
        r, _, ex, c = await self.attempt([response('not JSON')])
        self.assertEqual((r['status'], r['requests'], r['SQL_successes']), ('failed', 1, 0))
        self.assertEqual(ex.queries, [])
        self.assertEqual(c.responses.create.call_count, 1)
        self.assertEqual(r['continuation'], 'CONTINUE_KNOWN_TASK_FAILURE')

    async def test_missing_usage_stops_before_db(self):
        r, _, ex, _ = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}', usage=False)])
        self.assertIsNone(r['tokens'])
        self.assertEqual(r['continuation'], 'GLOBAL_STOP_UNKNOWN_USAGE')
        self.assertEqual(ex.queries, [])

    async def test_unsafe_sql_no_execution_no_answer(self):
        r, _, ex, c = await self.attempt([response('{"sql":"DELETE FROM invoices"}')])
        self.assertEqual(r['status'], 'unsupported')
        self.assertEqual(r['continuation'], 'GLOBAL_STOP_SQL_SAFETY')
        self.assertEqual(c.responses.create.call_count, 1)
        self.assertEqual(ex.queries, [])

    async def test_agent_sql_57014_failed_not_retried(self):
        from psycopg.errors import QueryCanceled
        r, _, ex, c = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}')], Executor(error=QueryCanceled('synthetic statement timeout')))
        self.assertEqual(r['exception']['sqlstate'], '57014')
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(r['continuation'], 'CONTINUE_KNOWN_TASK_FAILURE')
        self.assertEqual((len(ex.queries), c.responses.create.call_count), (1, 1))

    async def test_permission_error_stops(self):
        from psycopg.errors import InsufficientPrivilege
        r, _, _, _ = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}')], Executor(error=InsufficientPrivilege('synthetic permission failure')))
        self.assertEqual(r['continuation'], 'GLOBAL_STOP_DATABASE_AUTH_PERMISSION')

    async def test_preflight_failure_zero_model_calls(self):
        r, _, _, c = await self.attempt([], Executor(preflight_error=PermissionError('synthetic preflight failed')))
        self.assertEqual((r['requests'], c.responses.create.call_count), (0, 0))
        self.assertTrue(r['continuation'].startswith('GLOBAL_STOP'))

    async def test_answer_timeout_keeps_successful_sql_and_unknown_usage(self):
        r, trace, ex, c = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}'), TimeoutError()])
        self.assertEqual((r['requests'], r['SQL_successes'], c.responses.create.call_count), (2, 1, 2))
        self.assertEqual(r['continuation'], 'GLOBAL_STOP_UNKNOWN_USAGE')
        self.assertIsNone(r['tokens'])
        self.assertEqual(trace['output_data'], [{'amount': '41.25'}])
        self.assertTrue(ex.ended)

    async def test_known_request_token_budget_failure_no_second_call(self):
        r, _, ex, c = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}', total=17000)])
        self.assertEqual(r['tokens'], 17000)
        self.assertEqual((len(ex.queries), c.responses.create.call_count), (0, 1))
        self.assertEqual(r['continuation'], 'CONTINUE_KNOWN_TASK_FAILURE')

    async def test_complete_result_overflow_never_summarizes_preview(self):
        r, _, _, c = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}')], Executor(error=DEPS.app.ResultBudgetExceeded('synthetic too many rows')))
        self.assertEqual(r['status'], 'unsupported')
        self.assertIsNone(r['output_data'])
        self.assertEqual(c.responses.create.call_count, 1)

    async def test_extra_output_fields_not_accepted(self):
        r, _, ex, _ = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices","plan":"not allowed"}')])
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(ex.queries, [])

    async def test_incomplete_output_not_sent_to_sql(self):
        r, _, ex, _ = await self.attempt([response('{"sql":"SELECT total_cost FROM invoices"}', status='incomplete')])
        self.assertEqual(r['status'], 'failed')
        self.assertEqual(ex.queries, [])
