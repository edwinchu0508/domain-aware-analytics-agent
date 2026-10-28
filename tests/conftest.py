"""Offline tests must never contact a model, PostgreSQL, or telemetry endpoint."""
import os
os.environ['OPENAI_AGENTS_DISABLE_TRACING'] = '1'

import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    import psycopg
    import httpx2
    def forbidden(*args, **kwargs):
        raise AssertionError('Network/database access is forbidden in this offline suite')
    monkeypatch.setattr(psycopg, 'connect', forbidden)
    monkeypatch.setattr(httpx2.Client, 'send', forbidden)
    monkeypatch.setattr(httpx2.AsyncClient, 'send', forbidden)
