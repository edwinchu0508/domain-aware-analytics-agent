"""Portable backend adapter. Metadata/constraint queries retained from the evaluated backend.

No .env loading, original backend imports, data writes or inferred constraints.
"""
from contextlib import contextmanager
import os
import time
import psycopg
from psycopg.rows import dict_row
from .analytics_agent_v1 import ResultBudgetExceeded as SampleResourceLimit

class PostgresBackend:
    def __init__(self):
        required = ('DB_HOST','DB_PORT','DB_NAME','DB_USER','DB_PASSWORD')
        if any(not os.environ.get(k) for k in required):
            raise ValueError('Explicit database environment is required; .env is not loaded')
        self.DB_CONFIG = dict(host=os.environ['DB_HOST'], port=int(os.environ['DB_PORT']),
            dbname=os.environ['DB_NAME'], user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'],
            connect_timeout=5, row_factory=dict_row)

    def fetch_rows(self, query, params=None):
        """Original read-only DB boundary; use a read-only DB account too."""
        with psycopg.connect(**self.DB_CONFIG) as db:
            with db.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute(query, params)
                return cur.fetchall()

    def snapshot_schema(self) -> dict[str, dict[str, str]]:
        rows = self.fetch_rows("""
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        result: dict[str, dict[str, str]] = {}
        for row in rows:
            result.setdefault(row["table_name"], {})[row["column_name"]] = row["data_type"]
        if not result:
            raise ValueError("The live public schema is empty; check database configuration.")
        return result

    @contextmanager
    def metric_sample_rows(self, query: str):
        """Private complete-sample reader, NOT a tool accepting arbitrary model SQL.

        Server-side cursor bounds each batch; both the SQL statement and total read
        have a 5s budget. Exceeding a budget never returns a partial metric. Caller
        also enforces METRIC_MAX_SAMPLES. Closing the context releases cursor/txn.
        """
        with psycopg.connect(**self.DB_CONFIG) as db:
            with db.cursor() as control:
                control.execute("SET TRANSACTION READ ONLY")
                control.execute("SET LOCAL statement_timeout = '5s'")
            started = time.monotonic()
            with db.cursor(name="metric_samples_v1") as cur:
                cur.execute(query)

                def rows():
                    while True:
                        if time.monotonic() - started > 5:
                            raise SampleResourceLimit("Complete sample read exceeded 5s")
                        batch = cur.fetchmany(512)
                        if time.monotonic() - started > 5:
                            raise SampleResourceLimit("Complete sample read exceeded 5s")
                        if not batch:
                            return
                        yield from batch
                yield rows()

    def snapshot_unique_keys(self, schema: dict) -> dict:
        """Database constraint evidence only, never LLM/developer relationship names.

        Used lazily for a conditional JOIN proof. Does not infer foreign-key coverage
        or claim nullable keys preserve unmatched input rows. No table data is read.
        """
        rows = self.fetch_rows("""SELECT c.relname AS table_name,
            array_agg(a.attname ORDER BY k.ordinality) AS key_columns
            FROM pg_catalog.pg_constraint ct
            JOIN pg_catalog.pg_class c ON c.oid=ct.conrelid
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            CROSS JOIN LATERAL unnest(ct.conkey) WITH ORDINALITY k(attnum,ordinality)
            JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
            WHERE n.nspname='public' AND ct.contype IN ('p','u')
              AND ct.convalidated AND NOT ct.condeferrable AND NOT a.attisdropped
              AND c.relkind='r'
              AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_inherits i WHERE i.inhparent=c.oid)
            GROUP BY ct.oid,c.relname ORDER BY c.relname,ct.oid""")
        keys = {}
        for row in rows:
            if set(row) != {'table_name', 'key_columns'}:
                raise RuntimeError('Unexpected unique-key metadata shape')
            table, columns = row['table_name'], row['key_columns']
            if table not in schema:
                continue
            if not isinstance(columns, list) or not columns or any(not isinstance(c, str) or c not in schema[table] for c in columns):
                raise RuntimeError('Invalid unique-key metadata')
            keys.setdefault(table, []).append(columns)
        return keys
