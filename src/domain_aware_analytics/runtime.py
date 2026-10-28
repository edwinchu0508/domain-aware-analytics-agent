"""Extracted frozen budget, observation and Responses transport helpers.

Unused historical backend interception/deployment adapters are deliberately absent.
AnalyticsProvider overrides the inherited base model selector and event metadata.
"""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
from threading import Lock
import time
from agents import Model, ModelProvider, ModelSettings
from agents.retry import ModelRetrySettings

MODEL = "gpt-4.1-mini"  # inherited base only; final AnalyticsProvider pins its own model

@dataclass(frozen=True)
class Budgets:
    model_requests: int = 12
    tokens: int = 50_000
    wall_seconds: int = 120
    business_queries: int = 12
    all_db_reads: int = 20


class BudgetExceeded(RuntimeError):
    pass


def dumps(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=str)


def digest(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

@dataclass(frozen=True)
class Limits:
    request_seconds: float = 30
    output_tokens: int = 2048
    request_total_tokens: int = 16384  # observed usage; NOT a prepaid input-token cap
    model_requests: int = 12
    task_tokens: int = 50000
    task_seconds: float = 120
    cleanup_seconds: float = 5

LIMITS = Limits()

def safe(value):
    """Do not persist SDK raw items or unrestricted exception messages."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    value = json.loads(json.dumps(value, default=str, allow_nan=False))
    secrets = [os.getenv(name) for name in ("OPENAI_API_KEY", "DB_PASSWORD", "DB_HOST", "DB_NAME", "DB_USER")]
    def clean(item):
        if isinstance(item, dict):
            return {k: ("[exception message omitted]" if k in {"error", "error_message", "exception_message"} and isinstance(v, str)
                        else clean(v)) for k, v in item.items()}
        if isinstance(item, list):
            return [clean(v) for v in item]
        if isinstance(item, str):
            for secret in secrets:
                if secret and len(secret) >= 4:
                    item = item.replace(secret, "[REDACTED]")
        return item
    return clean(value)

class Observer:
    def __init__(self, emit, budgets=None):
        self.emit, self.budgets = emit, budgets or Budgets()
        self.started = time.monotonic()
        self.model_calls, self.db_calls, self.plans, self.reviews, self.validations = [], [], [], [], []
        self.contexts, self.budget_reason = [], None
        self.phase = ContextVar("evaluation_model_phase", default="unknown")
        self.runner_id = ContextVar("evaluation_runner_id", default=None)
        self.runner_count = 0
        self.db_lock = Lock()

    def event(self, kind, **data):
        self.emit(safe({"event": kind, **data}))

    def gate(self):
        if time.monotonic() - self.started > self.budgets.wall_seconds:
            self.budget_reason = "wall_time"
        if self.budget_reason:
            raise BudgetExceeded(self.budget_reason)

    def tokens(self):
        return sum(c["usage"].get("total_tokens", 0) for c in self.model_calls if c.get("usage"))

    def check_tokens(self):
        if self.tokens() > self.budgets.tokens:
            self.budget_reason = "tokens"
            raise BudgetExceeded("tokens")

class BoundedProvider(ModelProvider):
    def __init__(self, delegate, observer, *, limits=LIMITS):
        self.delegate, self.observer, self.limits = delegate, observer, limits
        self.deadline = time.monotonic() + limits.task_seconds - limits.cleanup_seconds
        self.calls = []

    def get_model(self, model_name):
        if model_name != MODEL:
            raise ValueError("Only the frozen gpt-4.1-mini model is permitted")
        return BoundedModel(self.delegate.get_model(model_name), self)

    async def aclose(self):
        await self.delegate.aclose()


class BoundedModel(Model):
    def __init__(self, delegate, owner):
        self.delegate, self.owner = delegate, owner

    async def get_response(self, *args, **kwargs):
        p, started = self.owner, time.monotonic()
        p.observer.gate()
        known = sum((c.get("usage") or {}).get("total_tokens", 0) for c in p.calls)
        if len(p.calls) >= p.limits.model_requests or known >= p.limits.task_tokens:
            raise BudgetExceeded("model_request_or_token_budget")
        remaining = p.deadline - started
        if remaining <= 0:
            raise TimeoutError("Task deadline reached")
        settings = kwargs.get("model_settings") or ModelSettings()
        kwargs["model_settings"] = replace(settings, max_tokens=p.limits.output_tokens,
                                           retry=ModelRetrySettings(max_retries=0))
        call = {"request_id": len(p.calls) + 1, "phase": p.observer.phase.get(), "model": MODEL,
                "status": "started", "usage": None, "deadline_seconds": min(p.limits.request_seconds, remaining),
                "max_output_tokens": p.limits.output_tokens, "max_total_tokens_observed": p.limits.request_total_tokens}
        p.calls.append(call)
        p.observer.event("request_started", **call)
        try:
            async with asyncio.timeout(call["deadline_seconds"]):
                result = await self.delegate.get_response(*args, **kwargs)
            usage = result.usage
            observed = {k: getattr(usage, k, None) for k in ("input_tokens", "output_tokens", "total_tokens")}
            if getattr(usage, "requests", 0) < 1 or any(type(v) is not int or v < 0 for v in observed.values()):
                raise BudgetExceeded("Request usage missing; cannot verify token budget")
            call.update(status="returned", usage=observed)
            call["usage"]["requests"] = 1
            details = getattr(usage, "input_tokens_details", None)
            call["cached_tokens"] = getattr(details, "cached_tokens", None)
            if (call["usage"]["total_tokens"] > p.limits.request_total_tokens
                    or call["usage"]["output_tokens"] > p.limits.output_tokens
                    or sum((c.get("usage") or {}).get("total_tokens", 0) for c in p.calls) > p.limits.task_tokens):
                raise BudgetExceeded("observed_request_or_task_tokens")
            return result
        except BaseException as exc:
            call.update(status="cancelled" if isinstance(exc, asyncio.CancelledError) else
                        "timeout" if isinstance(exc, TimeoutError) else "failed", exception_type=type(exc).__name__)
            raise  # original exception/cancellation semantics are preserved
        finally:
            call["elapsed_seconds"] = time.monotonic() - started
            p.observer.event("request_finished", **call)

    async def stream_response(self, *args, **kwargs):
        raise RuntimeError("Streaming is not enabled for this frozen evaluation")
        yield


def production_provider():
    """No IO at construction; called ONLY after the explicit live start gate."""
    import os
    import httpx2
    from openai import AsyncOpenAI
    from agents.models.openai_provider import OpenAIProvider
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], max_retries=0,
                         timeout=httpx2.Timeout(LIMITS.request_seconds, connect=5.0))
    return OpenAIProvider(openai_client=client, use_responses=True, use_responses_websocket=False), client
