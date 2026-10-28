"""Evaluated two-request E control, with portable imports and configuration."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.parse import quote, quote_plus
from types import SimpleNamespace
from . import analytics_agent_v1 as app, analytics_safety_v1 as safety
from .runtime import digest
HERE = Path(__file__).resolve().parent
SQL_FORMAT = {"type": "json_schema", "name": "single_sql", "strict": True,
              "schema": {"type": "object", "properties": {"sql": {"type": "string"}},
                         "required": ["sql"], "additionalProperties": False}}

def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)


def checkpoint(path, value):
    """Mutable only inside this newly created trial; final batch seal is separate."""
    payload = (dump(value) + "\n").encode("utf-8")
    temp = Path(path).with_suffix(".tmp")
    with temp.open("wb") as f:
        f.write(payload); f.flush(); os.fsync(f.fileno())
    temp.replace(path)


def response_usage(response):
    u = getattr(response, "usage", None)
    if u is None:
        return None, None
    values = {k: getattr(u, k, None) for k in ("input_tokens", "output_tokens", "total_tokens")}
    if any(type(v) is not int or v < 0 for v in values.values()) or values["input_tokens"] + values["output_tokens"] != values["total_tokens"]:
        return None, None
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", None)
    if cached is not None and (type(cached) is not int or not 0 <= cached <= values["input_tokens"]):
        return None, None
    return values, cached


async def run_one(question, executor, client, deps, trial):
    """Exactly one generation attempt and, only after success, one answer call."""
    from agents.exceptions import ModelBehaviorError
    from .runtime import BudgetExceeded
    cfg, prompts = deps.config, read(HERE / "prompts.json")
    started = time.monotonic()
    trace = {"variant": "E", "question": question, "terminal_status": "running", "status": "running",
             "model_calls": [], "SQL_attempts": [], "answer": None, "output_data": None,
             "errors": [], "repairs_used": 0, "verification_status": "NOT_REQUESTED", "verified": False,
             "planner_calls": 0, "reviewer_calls": 0, "dictionary_retrievals": 0,
             "config": cfg["common"], "e_max_requests": 2, "e_max_repairs": 0,
             "e_max_sql_attempts": 1, "e_max_verification_queries": 0,
             "schema_sha256": deps.protocol.digest(executor.schema)}
    error = None
    stage = "database_preflight"

    def save(event):
        trace["stage"] = event
        trace["requests"] = len(trace["model_calls"])
        trace["usage_known"] = all(c.get("usage") is not None for c in trace["model_calls"])
        trace["tokens"] = sum(c["usage"]["total_tokens"] for c in trace["model_calls"]) if trace["usage_known"] else None
        trace["latency_seconds"] = time.monotonic() - started
        checkpoint(trial / "agent_trace.json", trace)

    async def request(phase, instructions, payload, sql_format=False):
        if len(trace["model_calls"]) >= 2:
            raise BudgetExceeded("E two-request limit")
        call = {"request_id": len(trace["model_calls"]) + 1, "phase": phase,
                "model": cfg["common"]["model"], "reasoning_effort": cfg["common"]["reasoning"],
                "status": "started", "usage": None, "cached_tokens": None}
        trace["model_calls"].append(call)
        save("request_started")
        began = time.monotonic()
        try:
            kwargs = dict(model=cfg["common"]["model"], reasoning={"effort": cfg["common"]["reasoning"]},
                          max_output_tokens=cfg["common"]["max_output_tokens"],
                          instructions=instructions, input=[{"role": "user", "content": dump(payload)}],
                          tools=[], tool_choice="none", parallel_tool_calls=False, stream=False)
            if sql_format:
                kwargs["text"] = {"format": SQL_FORMAT}
            response = await asyncio.wait_for(client.responses.create(**kwargs), timeout=cfg["request_seconds"])
            call["usage"], call["cached_tokens"] = response_usage(response)
            call.update(status="returned", response_id=getattr(response, "id", None),
                        response_status=getattr(response, "status", None),
                        incomplete_details=getattr(getattr(response, "incomplete_details", None), "reason", None))
            if call["usage"] is None:
                raise BudgetExceeded("Unknown model usage; stop batch")
            if call["usage"]["total_tokens"] > cfg["request_total_tokens_observed"] or sum(c["usage"]["total_tokens"] for c in trace["model_calls"]) > cfg["common"]["max_tokens"]:
                raise BudgetExceeded("Observed token limit exceeded")
            if response.status != "completed":
                raise ModelBehaviorError("E model response incomplete")
            output = getattr(response, "output", [])
            if any(getattr(item, "type", None) not in {"reasoning", "message"} for item in output):
                raise ModelBehaviorError("E unexpected response output type")
            text = getattr(response, "output_text", "")
            if not isinstance(text, str) or not text.strip():
                raise ModelBehaviorError("E empty output or refusal")
            return text
        except BaseException as exc:
            call["exception_type"] = type(exc).__name__
            if call["usage"] is None:
                call["status"] = "timeout" if isinstance(exc, TimeoutError) else "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            raise
        finally:
            call["elapsed_seconds"] = time.monotonic() - began
            save("request_finished")

    async def pipeline():
        nonlocal stage
        executor.preflight()
        trace["database_snapshot"] = executor.begin_evidence_session()
        save("database_snapshot_opened")
        stage = "sql_generation"
        raw = await request(stage, prompts["sql_system"], {"question": question, "schema": executor.schema}, True)
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            raise ModelBehaviorError("E SQL JSON parse failed") from None
        if not isinstance(value, dict) or set(value) != {"sql"} or not isinstance(value["sql"], str):
            raise ModelBehaviorError("E SQL output schema mismatch")
        query = value["sql"]
        attempt = {"query": query, "status": "generated", "rows": None, "safety": {}}
        trace["SQL_attempts"].append(attempt)
        stage = "sql_safety"
        safety = deps.safety.validate_sql_safety(query, executor.schema, max_output_rows=cfg["common"]["max_rows"])
        attempt["safety"] = safety.model_dump(mode="json")
        if safety.status != "safe":
            attempt["status"] = "rejected"
            trace.update(status="unsupported", failure_reason=safety.code)
            return
        stage = "agent_sql"
        attempt["status"] = "execution_started"
        save("sql_execution_started")
        try:
            rows = executor.execute(query, max_rows=cfg["common"]["max_rows"], max_bytes=cfg["common"]["max_result_bytes"])
        except deps.app.ResultBudgetExceeded as exc:
            attempt["status"] = "result_budget_exceeded"
            trace.update(status="unsupported", failure_reason="COMPLETE_RESULT_BUDGET")
            trace["errors"].append(deps.protocol.error_record(exc, "agent_sql_result_budget"))
            return
        except BaseException:
            attempt["status"] = "execution_error"
            raise
        attempt.update(status="executed", rows=rows, complete=True)
        trace["output_data"] = rows
        save("sql_executed")
        stage = "answer_generation"
        trace["answer"] = await request(stage, prompts["answer_system"],
            {"question": question, "schema": executor.schema, "executed_sql": query,
             "complete_result": rows, "row_count": len(rows)})
        trace["status"] = "completed"

    try:
        await asyncio.wait_for(pipeline(), timeout=cfg["common"]["task_seconds"])
    except BaseException as exc:
        error = deps.protocol.error_record(exc, stage, agent_sql_error=stage == "agent_sql")
        trace["errors"].append(error)
        trace["status"] = "failed"
    finally:
        try:
            executor.end_evidence_session()
        except BaseException as exc:
            error = deps.protocol.error_record(exc, "database_cleanup")
            trace["errors"].append(error)
            trace["status"] = "failed"
        trace["terminal_status"] = trace["status"]
        save("terminal")
    continuation = deps.protocol.classify(trace, error)
    trace["continuation"] = continuation
    cost, cache_unknown = deps.protocol.estimated_cost(trace["model_calls"])
    trace.update(estimated_cost_usd=cost, cache_usage_missing_assumed_zero=cache_unknown, actual_billed_cost="UNKNOWN")
    save("sealed_trial_ready")
    return {"variant": "E", "status": trace["status"], "final_answer": trace["answer"],
            "output_data": trace["output_data"], "requests": trace["requests"], "tokens": trace["tokens"],
            "usage_known": trace["usage_known"], "estimated_cost_usd": cost,
            "latency_seconds": trace["latency_seconds"], "repairs_used": 0,
            "SQL_attempts": len(trace["SQL_attempts"]),
            "SQL_successes": sum(a["status"] == "executed" for a in trace["SQL_attempts"]),
            "verified": False, "verification_status": "NOT_REQUESTED", "task_correctness": "EXTERNAL_GRADER_ONLY",
            "exception": error, "failure_reason": trace.get("failure_reason"), "continuation": continuation,
            "source_trace": str(trial / "agent_trace.json")}

def estimated_cost(calls):
    """Usage-based Standard-tier estimate; unknown usage never becomes zero."""
    total=0;cache_assumption=False
    for call in calls:
        u=call.get('usage')
        if u is None:return None,True
        cached=call.get('cached_tokens')
        if cached is None:cached=0;cache_assumption=True
        if not 0<=cached<=u['input_tokens']:raise ValueError('Invalid cache usage')
        total+=((u['input_tokens']-cached)*2+cached*.1+u['output_tokens']*10)/1000000
    return total,cache_assumption


def exception_chain(exc):
    """Retain wrapped database causes; a cyclic cause must not hang logging."""
    chain=[];seen=set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc));chain.append(exc);exc=getattr(exc,'__cause__',None)
    return chain


def redact_error_text(value):
    if value is None:return None
    text=str(value)
    for name in ('DB_PASSWORD','OPENAI_API_KEY','PGPASSWORD'):
        secret=os.environ.get(name)
        if secret:
            for spelling in sorted({secret,quote(secret,safe=''),quote_plus(secret)},key=len,reverse=True):
                text=text.replace(spelling,'[REDACTED]')
    text=re.sub(r'(?i)(password\s*=\s*)(?:\'[^\']*\'|"[^"]*"|[^\s;]+)',r'\1[REDACTED]',text)
    return re.sub(r'(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)',r'\1[REDACTED]\2',text)


def error_record(exc,stage,*,agent_sql_error=False):
    """Error evidence only: preserve DB messages, redact process credentials.

    Never serialize arbitrary SDK exception text or raw model responses.
    A real DB error and executor provenance are both required for continuation.
    """
    import psycopg
    chain=exception_chain(exc)
    databases=[item for item in chain if isinstance(item,psycopg.Error)]
    with_state=[item for item in databases if getattr(item,'sqlstate',None)]
    cause=(with_state or databases or chain)[-1]
    db=isinstance(cause,psycopg.Error)
    record=dict(type=type(cause).__name__,outer_type=type(exc).__name__,
        cause_types=[type(item).__name__ for item in chain],sqlstate=getattr(cause,'sqlstate',None),
        stage=stage,database_error=db,agent_sql_error=bool(db and agent_sql_error),
        database_message=redact_error_text(str(cause)) if db else None,
        message_policy='Full DB exception text except credential redaction; no raw SDK/model output')
    if db:
        diag=getattr(cause,'diag',None)
        record['database_diagnostics']={name:redact_error_text(getattr(diag,name,None)) for name in
            ('severity_nonlocalized','message_primary','message_detail','message_hint',
             'context','statement_position','schema_name','table_name','column_name')}
    return record


def classify(trace,error):
    """003 amendment: only evidenced Agent SQL errors gain continuation."""
    if not trace or not trace.get('usage_known') or any(c.get('usage') is None for c in trace.get('model_calls',[])):
        return 'GLOBAL_STOP_UNKNOWN_USAGE'
    if any(a.get('safety',{}).get('category')=='unsafe' for a in trace.get('SQL_attempts',[])):
        return 'GLOBAL_STOP_SQL_SAFETY'
    # Transport failures stop even if some usage happens to be reported.
    if any(c.get('status') in {'timeout','cancelled'} or c.get('exception_type') in
           {'AuthenticationError','PermissionDeniedError','APIConnectionError','APITimeoutError','RateLimitError','APIStatusError'}
           for c in trace.get('model_calls',[])):
        return 'GLOBAL_STOP_TRANSPORT'
    if error is None:return 'CONTINUE'
    state=error.get('sqlstate')
    if state is not None:
        if not isinstance(state,str) or not re.fullmatch('[0-9A-Z]{5}',state):
            return 'GLOBAL_STOP_UNCLASSIFIED_OR_DATABASE_ERROR'
        if state.startswith('08'):return 'GLOBAL_STOP_DATABASE_CONNECTION'
        if state.startswith('28') or state=='42501':return 'GLOBAL_STOP_DATABASE_AUTH_PERMISSION'
        if state.startswith('53'):return 'GLOBAL_STOP_DATABASE_RESOURCE'
        if state.startswith('57P'):return 'GLOBAL_STOP_DATABASE_SERVER_SHUTDOWN'
        eligible=state.startswith(('42','22')) or state=='57014'
        if eligible and error.get('database_error') is True and error.get('agent_sql_error') is True and error.get('stage')=='agent_sql':
            return 'CONTINUE_KNOWN_TASK_FAILURE'
        return 'GLOBAL_STOP_UNCLASSIFIED_OR_DATABASE_ERROR'
    if error.get('type') in {'BudgetExceeded','MaxTurnsExceeded','ModelBehaviorError','TimeoutError'}:
        return 'CONTINUE_KNOWN_TASK_FAILURE'
    return 'GLOBAL_STOP_UNCLASSIFIED_OR_DATABASE_ERROR'

def dependencies():
    config = read(HERE / 'experiment_config.json')
    protocol = SimpleNamespace(digest=digest, error_record=error_record,
        classify=classify, estimated_cost=estimated_cost)
    return SimpleNamespace(config=config, protocol=protocol, app=app, safety=safety)
