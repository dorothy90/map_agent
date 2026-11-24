"""Verify stored page contents and optionally read the final row through a stock Hermes chat."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

from dotenv import load_dotenv

from harness.config import Settings
from harness.control import RunController
from harness.executor import ExecutionContext, ToolExecutor
from harness.native_tools import domain_model_result
from harness.store import HarnessStore
from harness.tools.registry import domain_registry


async def evaluate(*, live_model=False):
    load_dotenv()
    settings = Settings.from_env()
    store = HarnessStore(settings.mongo_uri, 'harness_page_eval_' + uuid.uuid4().hex)
    report = {'data_origin': 'fixture', 'live_model': live_model}
    try:
        await store.setup()
        marker = uuid.uuid4().hex
        rows = [{'n': n, 'value': uuid.uuid4().hex} for n in range(1, 159)]
        rows[-1]['value'] = marker
        principal, session = 'page-eval-' + uuid.uuid4().hex, uuid.uuid4().hex
        source = await store.put_result(principal, session, {
            'run_id': session, 'invocation_id': 'source', 'tool_name': 'fixture',
            'provenance': {'data_origin': 'fixture'}}, rows, [])
        prompt = (f'Read result {source.result_id} with offset 0 and limit 158. '
                  'Give the exact value of the row whose n is 158.')
        settings = settings.model_copy(update={'native_allowed_tools': ['read_result']})
        if live_model:
            controller = RunController(store, settings)
            try:
                run = await controller.start(principal, session, 'page-eval', prompt)
                await controller.tasks[run['run_id']]
                run = await store.get_run(principal, run['run_id'])
                assert run['status'] == 'completed' and run['native_engine']['entrypoint'] == 'stock_chat'
                mentioned = {row["value"] for row in rows if row["value"] in run["answer"]}
                report.update(model=settings.model, run_id=run['run_id'], principal=principal, session_id=session,
                              last_row_verified=mentioned == {marker}, bare_value_only=run['answer'].strip() == marker)
                assert report['last_row_verified'], 'Model did not return the last row value'
                call = await store.calls.find_one({'run_id': run['run_id'], 'tool_name': 'read_result',
                                                   'status': 'succeeded', 'arguments.result_id': source.result_id,
                                                   'arguments.limit': 158})
                assert call, 'Expected read_result with limit 158'
                saved = await store.observation(principal, session, call['result_id'])
            finally:
                await controller.close()
        else:
            run = await store.start_run(principal, session, 'page-eval', prompt)
            run = await store.acquire(run['run_id'], 'page-eval')
            executor = ToolExecutor(domain_registry(), ExecutionContext(store, settings, run))
            observation = await executor.execute({'name': 'read_result', 'id': 'read',
                'args': {'result_id': source.result_id, 'offset': 0, 'limit': 158}})
            saved = await store.observation(principal, session, observation.result_id)
            assert saved.model_view() == domain_model_result(observation), 'Reload changed model payload'
        payload = domain_model_result(saved)
        report.update(requested_rows=158, model_rows=len(payload['preview_rows']),
            summary=payload['summary'], reload_rows=len(saved.model_view()['preview_rows']),
            tool_status=payload['status'])
        assert payload['preview_rows'] == rows, report
        report['passed'] = True
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__)
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--live-model', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(evaluate(live_model=args.live_model))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
