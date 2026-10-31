"""Exercise persisted pages through the native bridge, optionally with a live model."""
import argparse
import asyncio
import json
from pathlib import Path
import uuid

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, ToolMessage

from harness.config import Settings
from harness.executor import ExecutionContext
from harness.model import build_model
from harness.native_hermes import NativeBridge
from harness.store import HarnessStore
from harness.tools.python_tools import ReadInput, read_result
from harness.tools.registry import ToolRegistry, ToolSpec


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
        run = await store.start_run('page-eval', 'page-eval', 'page-eval', 'Read 158 rows')
        source = await store.put_result('page-eval', 'page-eval', {
            'run_id': run['run_id'], 'invocation_id': 'source', 'tool_name': 'fixture',
            'provenance': {'data_origin': 'fixture'}}, rows, [])
        registry = ToolRegistry()
        registry.add(ToolSpec('read_result', 'Read a page from a stored result.', ReadInput, read_result))
        request = HumanMessage(content=(f'Read result {source.result_id} with offset 0 and limit 158. '
            'Then return only the exact value of the row whose n is 158. Do not guess a missing value.'))
        messages = [request]
        if live_model:
            model = build_model(settings.model_copy(update={'max_output_tokens': 1024}))
            decision = await model.bind_tools(registry.model_tools(), tool_choice='read_result').ainvoke(messages)
            assert len(decision.tool_calls) == 1, 'Expected one read_result tool call'
            call = decision.tool_calls[0]
            messages.append(decision)
        else:
            call = {'name': 'read_result', 'id': 'read', 'args': {'result_id': source.result_id, 'limit': 158}}
        assert call['name'] == 'read_result' and call['args'].get('limit') == 158
        run = await store.acquire(run['run_id'], 'page-eval')
        bridge = NativeBridge(registry, ExecutionContext(store, settings, run))
        payload = await bridge.invoke(call)
        saved = await store.observation('page-eval', 'page-eval', payload['result_id'])
        report.update(requested_rows=158, model_rows=len(payload['preview_rows']),
            summary=payload['summary'], reload_rows=len(saved.model_view()['preview_rows']),
            tool_status=payload['status'])
        assert payload['preview_rows'] == rows, report
        assert saved.model_view() == payload, 'Reload changed model payload'
        if live_model:
            messages.append(ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id=call['id']))
            answer = await model.ainvoke(messages)
            report.update(model=settings.model, last_row_verified=answer.content.strip() == marker)
            assert report['last_row_verified'], 'Model did not return the last row value'
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
