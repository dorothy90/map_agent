import asyncio
import hashlib
from copy import deepcopy

import pytest

from harness.gateway.events import project_run_event, verified_answer_with_artifacts
from harness.store import HarnessStore
from harness.types import ToolObservation


def observation():
    return ToolObservation(principal_id='p', session_id='s /한', run_id='r',
        invocation_id='i', result_id='result', tool_name='plot', artifact_refs=[
            {'artifact_id': f'a/{i}', 'title': 'Map [x]\\\n*plot*',
             'mime': 'image/png' if i < 6 else 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
            for i in range(7)])


def test_model_gets_every_artifact_with_copyable_escaped_links():
    obs = observation()
    refs = obs.model_view()['artifact_refs']
    assert len(refs) == 7
    assert refs[6]['href'] == '/harness/artifacts/a%2F6?session_id=s+%2F%ED%95%9C'
    assert refs[0]['markdown'] == '![Map \\[x\\]\\\\ \\*plot\\*](/harness/artifacts/a%2F0?session_id=s+%2F%ED%95%9C)'
    assert refs[6]['markdown'].startswith('[Map ')
    assert all(ref['result_id'] == 'result' for ref in refs)
    assert ToolObservation.model_validate(obs.model_dump()).model_view()['artifact_refs'] == refs
    assert 'href' not in obs.artifact_refs[0]


def completed(answer, policy=None):
    run = {'session_id': 's', 'active': False, 'status': 'completed', 'answer_text': answer,
        'observations': [{'artifact_refs': [{'artifact_id': 'draft', 'title': 'Draft', 'mime': 'image/png'}]},
            {'tool_name': 'verify_answer', 'scope': {'verdict': 'pass'},
             'validated_arguments': {'candidate_sha256': hashlib.sha256(answer.encode()).hexdigest()}}]}
    if policy is not None:
        run['artifact_delivery'] = policy
    return run


@pytest.mark.parametrize('answer', ['No file is needed.', '![Final](/harness/artifacts/final?session_id=s)'])
def test_answer_refs_delivers_exact_verified_answer(answer):
    run = completed(answer, 'answer_refs')
    assert verified_answer_with_artifacts(run) == answer
    assert project_run_event(run, {'type': 'run_finished'})['payload']['text'] == answer
    run['answer_text'] += 'unverified'
    assert verified_answer_with_artifacts(run) is None


def test_unmarked_history_keeps_legacy_artifacts():
    assert verified_answer_with_artifacts(completed('Answer')) == 'Answer\n\n![Draft](/harness/artifacts/draft?session_id=s)'


def test_stock_chat_publishes_settled_answer_on_finish_and_replay():
    answer = '[수율 표](/harness/artifacts/table?session_id=s)'
    run = {**completed(answer, 'answer_refs'), 'observations': [],
           'native_engine': {'entrypoint': 'stock_chat'}}
    assert project_run_event(run, {'type': 'run_finished'})['payload']['text'] == answer
    assert verified_answer_with_artifacts(run) == answer
    assert verified_answer_with_artifacts({**run, 'active': True}) is None
    assert verified_answer_with_artifacts({**run, 'status': 'failed'}) is None
    assert verified_answer_with_artifacts({**run, 'native_engine': {}}) is None


@pytest.mark.parametrize('engine,expected', [('hermes', 'answer_refs'), ('langgraph', None)])
def test_admission_pins_new_policy_without_upgrading_receipts(engine, expected):
    class Runs:
        row = None
        async def find_one(self, key):
            return deepcopy(self.row)
        async def insert_one(self, row):
            self.row = deepcopy(row)
    store = object.__new__(HarnessStore)
    store.runs = Runs()
    async def exercise():
        run = await store.start_run('p', 's', 'req', 'q', engine=engine)
        assert run.get('artifact_delivery') == expected
        store.runs.row.pop('artifact_delivery', None)
        old = await store.start_run('p', 's', 'req', 'q', engine='hermes')
        assert 'artifact_delivery' not in old
    asyncio.run(exercise())


def test_model_links_preserve_html_and_download_artifacts():
    obs = observation()
    obs.artifact_refs = [
        {'artifact_id': 'html', 'title': '<report> &amp; ![label]', 'mime': 'text/html'},
        {'artifact_id': 'svg', 'title': 'Vector', 'mime': 'image/svg+xml'},
        {'artifact_id': 'pdf', 'title': 'PDF', 'mime': 'application/pdf'},
    ]
    links = obs.model_view()['artifact_refs']
    assert links[0]['markdown'] == '[\\<report\\> \\&amp; \\!\\[label\\]](/harness/artifacts/html?session_id=s+%2F%ED%95%9C)'
    assert all(link['markdown'].startswith('[') for link in links)
    assert [link['mime'] for link in links] == ['text/html', 'image/svg+xml', 'application/pdf']
