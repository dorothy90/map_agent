import asyncio
from contextlib import asynccontextmanager
import uuid

import pytest

from harness.config import Settings
from harness.context import descriptor, evidence_views
from harness.executor import ExecutionContext
from harness.native_hermes import NativeBridge
from harness.store import HarnessStore
from harness.tools.python_tools import ReadInput, read_result
from harness.tools.registry import ToolRegistry, ToolSpec
from harness.types import ResultTable


@asynccontextmanager
async def reading(rows, *, complete=True):
    store = HarnessStore(database='harness_page_test_' + uuid.uuid4().hex)
    try:
        await store.setup()
        source = await store.put_result('p', 's', {'run_id': 'source', 'invocation_id': 'source',
            'tool_name': 'query', 'status': 'success' if complete else 'partial',
            'scope': {}}, [], [],
            tables=[ResultTable(table_id='facts', title='Facts', columns=['n'], rows=rows,
                                complete=complete, missing_reason=None if complete else 'limited')])
        run = await store.start_run('p', 's', 'request', 'Read a stored page')
        run = await store.acquire(run['run_id'], 'test')
        registry = ToolRegistry()
        registry.add(ToolSpec('read_result', 'Read', ReadInput, read_result))
        args = {'result_id': source.result_id, 'table_id': 'facts'}
        bridge = NativeBridge(registry, ExecutionContext(store, Settings(), run))
        yield store, source, bridge, args
    finally:
        await store.client.drop_database(store.db.name)
        store.client.close()


def shown(view):
    return view['preview_rows']


def test_wafer_report_uses_full_precision_and_exact_source_identifiers():
    from html.parser import HTMLParser
    from harness.tools.wafer_group_report import wafer_group_report

    rows = [dict(LOT_ID='X&Y', PARAM_NM='Yield', WFID=wf, VALUE=value)
            for wf, value in [('15', 0.702), ('14', 0.6999), ('13', 0.7)]]
    caption, report = wafer_group_report(rows, 70.0)

    class Text(HTMLParser):
        def handle_data(self, value):
            parts.append(value)

    parts = []
    Text().feed(report['data'])
    assert report['title'] == 'X&Y · Yield 수율'
    assert caption == '70% 미만 1장 (평균 70.0%) · 70% 이상 2장 (평균 70.1%)'
    assert caption in ''.join(parts)
    assert '<strong>70% 미만 1장</strong>' in report['data']
    assert parts.index('70.0% 미만') < parts.index('70.0% 이상')
    assert '13, 15' in parts and '14' in parts
    assert '13~15' not in parts
    with pytest.raises(ValueError):
        wafer_group_report(rows + rows[:1], 70)


def test_read_result_renders_only_a_complete_wafer_table():
    async def scenario():
        rows = [dict(LOT_ID='LOT-B', PARAM_NM='Y', WFID=str(wf), VALUE=0.6) for wf in (2, 5)]
        async with reading(rows) as (store, source, bridge, args):
            report = {'threshold_pct': 65, 'delivery': 'complete_answer'}
            response = await bridge.invoke({'id': 'report', 'name': 'read_result',
                                            'args': {**args, 'wafer_report': report}})
            assert response['source_result_ids'] == [source.result_id]
            assert response['artifact_refs'][0]['title'] == 'LOT-B · Y 수율'
            assert response['scope']['rendered_summary'] == '65% 미만 2장 (평균 60.0%) · 65% 이상 0장 (평균 —)'
            assert len(response['artifact_refs']) == 1
            partial = await bridge.invoke({'id': 'partial', 'name': 'read_result',
                                           'args': {**args, 'limit': 1, 'wafer_report': report}})
            assert partial['status'] == 'error' and not partial['artifact_refs']
    asyncio.run(scenario())


def test_requested_rows_survive_store_bridge_and_replay():
    async def scenario():
        rows = [{'n': n} for n in range(158)]
        async with reading(rows) as (store, source, bridge, args):
            call = {'id': 'read', 'name': 'read_result', 'args': {**args, 'limit': 158}}
            view = await bridge.invoke(call)
            assert shown(view) == rows
            assert view['summary'] == '원본 158행 중 offset=0부터 158행 표시'
            assert view['page']['visible_rows'] == 158
            assert view['page']['read_rows'] == 158
            reloaded = await store.observation('p', 's', view['result_id'])
            assert reloaded.model_view() == view
            index = descriptor(reloaded.model_dump(mode='json'))
            assert index['page']['visible_rows'] == 0
            assert index['summary'] == '원본 158행 중 offset=0부터 0행 표시'
            failed = reloaded.model_copy(update={'status': 'error', 'summary': 'failed'})
            assert not shown(failed.model_view())
            assert failed.model_view()['summary'] == 'failed'
            assert await bridge.invoke(call) == view
            assert len(source.preview_rows) == 50
            assert len(source.model_view()['preview_rows']) == 10
            projected = evidence_views([reloaded.model_dump(mode='json')], token_budget=30)[0]
            assert 0 < len(shown(projected)) < 158
            assert projected['summary'] == f"원본 158행 중 offset=0부터 {len(shown(projected))}행 표시"
            assert projected['page']['visible_rows'] == len(shown(projected))
            assert projected['page']['next_offset'] == len(shown(projected))
    asyncio.run(scenario())


@pytest.mark.parametrize('offset,limit,expected', [(10,20,20), (150,50,8), (158,50,0), (170,50,0), (0,1000,158)])
def test_page_summary_tracks_offsets_and_empty_pages(offset, limit, expected):
    async def scenario():
        async with reading([{'n': n} for n in range(158)]) as (_, _, bridge, args):
            view = await bridge.invoke({'id': 'read', 'name': 'read_result',
                'args': {**args, 'offset': offset, 'limit': limit}})
            assert len(shown(view)) == expected
            assert view['summary'] == f'원본 158행 중 offset={offset}부터 {expected}행 표시'
            assert view['page']['next_offset'] == (offset + expected if offset + expected < 158 else None)
    asyncio.run(scenario())


def test_page_byte_limit_reports_visible_rows_without_changing_stored_rows():
    async def scenario():
        rows = [{'n': n, 'text': 'x' * 20000} for n in range(100)]
        async with reading(rows) as (store, _, bridge, args):
            view = await bridge.invoke({'id': 'read', 'name': 'read_result', 'args': {**args, 'limit': 100}})
            count = len(shown(view))
            assert 10 < count < 100
            assert view['summary'] == f'원본 100행 중 offset=0부터 {count}행 표시'
            assert view['page']['next_offset'] == count
            assert view['truncated']
            stored = await store.rows('p', 's', view['result_id'], table_id='facts')
            assert stored == rows[:len(stored)]
            assert view['page']['read_rows'] == len(stored)
    asyncio.run(scenario())


def test_maximum_page_and_invalid_limit():
    async def scenario():
        async with reading([{'n': n} for n in range(1001)]) as (_, _, bridge, args):
            view = await bridge.invoke({'id': 'read', 'name': 'read_result', 'args': {**args, 'limit': 1000}})
            assert len(shown(view)) == 1000
            invalid = await bridge.invoke({'id': 'invalid', 'name': 'read_result', 'args': {**args, 'limit': 1001}})
            assert invalid['status'] == 'error'
            assert not invalid['preview_rows']
    asyncio.run(scenario())


def test_reading_all_rows_does_not_make_incomplete_source_complete():
    async def scenario():
        async with reading([{'n': n} for n in range(20)], complete=False) as (_, _, bridge, args):
            view = await bridge.invoke({'id': 'read', 'name': 'read_result', 'args': {**args, 'limit': 20}})
            assert view['status'] == 'partial'
            assert len(shown(view)) == 20
            target = next(table for table in view['tables'] if table['table_id'] == ('facts'))
            assert target['complete'] is False
            assert target['missing_reason'] == 'limited'
    asyncio.run(scenario())
