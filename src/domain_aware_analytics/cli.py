"""Portable single-session entry point; live requests require --live explicitly."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from .analytics_agent_v1 import AnalyticsConfig, ReadonlyExecutor, aask_analytics
from .runtime import production_provider, safe


async def run(args):
    from .postgres import PostgresBackend
    from .baseline_e import dependencies, run_one
    # New directory is a duplicate-start guard. Never resume or overwrite results.
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    executor = ReadonlyExecutor(PostgresBackend())
    executor.preflight()  # fail before creating a paid client
    provider, client = production_provider()
    try:
        if args.variant == 'E':
            result = await run_one(args.question, executor, client, dependencies(), output)
        else:
            result = await aask_analytics(args.question, executor=executor,
                config=AnalyticsConfig(variant=args.variant),
                trace_path=output / 'agent_trace.jsonl', model_provider=provider)
        payload = safe(result)
        (output / 'prediction.json').write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str)+'\n', encoding='utf-8')
        print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        return 0 if result.get('status') == 'completed' else 1
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variant', choices=list('ABCDE'), default='D')
    parser.add_argument('--question', required=True)
    parser.add_argument('--output', required=True, help='New local directory; never publish its raw contents')
    parser.add_argument('--live', action='store_true', help='Allow paid model calls and read-only PostgreSQL access')
    args = parser.parse_args()
    if not args.live:
        parser.error('Live execution is disabled; supply --live deliberately, or run the offline example')
    os.environ['OPENAI_AGENTS_DISABLE_TRACING'] = '1'
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # DB/SDK exception strings may contain SQL, hostnames or credentials.
        print(json.dumps({'status':'failed','exception_type':type(exc).__name__,
                          'sqlstate':getattr(exc,'sqlstate',None),'message':'See local trace if the session started; raw exception omitted'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
