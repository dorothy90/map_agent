import asyncio

import pytest

from harness.artifact_delivery import artifact_url
from harness.native_verification import (
    candidate_artifact_references, enforce_wafer_map_contract_review,
    inspect_answer_artifacts, inspect_wafer_map_contracts,
)
from harness.types import ToolObservation
from harness.tests.test_wafer_map_aggregation_verification import (
    SOURCE_ROWS, MemoryStore, observation, table_ref, valid_tables,
)


class ArtifactStore:
    def __init__(self):
        self.ref = {'artifact_id': 'a', 'result_id': 'old', 'mime': 'image/png', 'title': 'Map'}
        self.item = ToolObservation(principal_id='p', session_id='s', run_id='earlier-turn',
            invocation_id='i', result_id='old', tool_name='run_python', artifact_refs=[self.ref.copy()])
        self.missing_blob = False

    async def artifact(self, principal, session, artifact_id):
        if (principal, session, artifact_id) != ('p', 's', 'a') or self.missing_blob:
            raise PermissionError('unavailable')
        return self.ref, b'image'

    async def observation(self, principal, session, result_id):
        if (principal, session, result_id) != ('p', 's', 'old'):
            raise PermissionError('unavailable')
        return self.item


def inspect(store, answer, principal='p', session='s'):
    return asyncio.run(inspect_answer_artifacts(store, principal, session, answer))


def test_structural_links_ignore_code_and_external_lookalikes():
    href = artifact_url('s', 'a')
    text = f'`[x]({href})`\n\n```md\n![x]({href})\n```\n\n[x](https://example.com{href})\n\n![map][ref]\n\n[ref]: {href}'
    assert candidate_artifact_references(text) == [{'href': href, 'image': True}]


def test_prior_turn_owned_reference_resolves_and_duplicates_check_once():
    href = artifact_url('s', 'a')
    report = inspect(ArtifactStore(), f'![map]({href}) [download]({href})')
    assert report['verdict'] == 'pass'
    assert report['selected_result_ids'] == ['old']
    assert report['observations'][0]['run_id'] == 'earlier-turn'


@pytest.mark.parametrize('href', [
    '/harness/artifacts/a?session_id=other', '/harness/artifacts/a',
    '/harness/artifacts/a?session_id=s&session_id=s', '/harness/artifacts/a?session_id=s#fragment',
    '/harness/artifacts/missing?session_id=s', '/harness/artifacts/../a?session_id=s',
    '/harness/artifacts/a?session_id=s&extra=1',
])
def test_forged_reference_fails(href):
    assert inspect(ArtifactStore(), f'[file]({href})')['verdict'] == 'fail'


@pytest.mark.parametrize('case', ['blob', 'owner', 'status', 'mime', 'lineage', 'membership', 'image'])
def test_unavailable_or_inconsistent_stored_artifact_fails(case):
    store = ArtifactStore()
    if case == 'blob': store.missing_blob = True
    if case == 'owner': store.item.principal_id = 'other'
    if case == 'status': store.item.status = 'error'
    if case == 'mime': store.ref['mime'] = 'text/html'
    if case == 'lineage': store.item.source_result_ids = ['missing']
    if case == 'membership': store.item.artifact_refs = []
    if case == 'image':
        store.ref['mime'] = 'text/html'
        store.item.artifact_refs[0]['mime'] = 'text/html'
    report = inspect(store, f'![map]({artifact_url("s", "a")})')
    assert report['verdict'] == 'fail'
    assert enforce_wafer_map_contract_review(
        {'verdict': 'pass', 'issues': [], 'result_ids': []}, report)['verdict'] == 'fail'


def test_no_link_narrative_is_valid_and_other_principal_cannot_reuse_link():
    assert inspect(ArtifactStore(), 'Hello')['verdict'] == 'pass'
    assert inspect(ArtifactStore(), '[map](/harness/artifacts/a?session_id=s)', principal='other')['verdict'] == 'fail'


def test_selected_map_not_latest_map_and_multiple_same_source_results():
    source = observation('source', 'load_wafer_maps')
    tables = valid_tables()
    correct = observation('correct', 'run_python', source_ids=['source'],
        tables=[table_ref(name, rows) for name, rows in tables.items()],
        artifacts=[{'artifact_id': 'correct-chart'}])
    wrong = observation('wrong', 'run_python', source_ids=['source'],
        tables=[table_ref('output', [{'stdout': 'wrong'}])], artifacts=[{'artifact_id': 'wrong-chart'}])
    stored = {('source', 'wafer_map_dies'): SOURCE_ROWS, ('wrong', 'output'): [{'stdout': 'wrong'}]}
    stored.update({('correct', name): rows for name, rows in tables.items()})
    store = MemoryStore(stored)
    def review(selected):
        return asyncio.run(inspect_wafer_map_contracts(store, 'p', 's',
            [source, wrong, correct], selected_result_ids=selected))
    assert review(['wrong'])['verdict'] == 'fail'
    assert review(['correct'])['verdict'] == 'pass'
    both = review(['wrong', 'correct'])
    assert both['verdict'] == 'fail'
    assert set(both['result_ids']) == {'source', 'wrong', 'correct'}
    assert review([])['verdict'] == 'not_applicable'


def test_prior_turn_lineage_is_loaded_and_exposed_to_review():
    store = ArtifactStore()
    store.item.source_result_ids = ['source']
    source = ToolObservation(principal_id='p', session_id='s', run_id='older-turn',
        invocation_id='source', result_id='source', tool_name='load_wafer_maps')
    original = store.observation
    async def get(principal, session, result_id):
        return source if result_id == 'source' else await original(principal, session, result_id)
    store.observation = get
    report = inspect(store, '[map](/harness/artifacts/a?session_id=s)')
    assert report['verdict'] == 'pass'
    assert report['result_ids'] == ['old', 'source']
    assert report['selected_result_ids'] == ['old']


def test_encoded_reference_preserves_exact_canonical_url():
    href = artifact_url('session / +', 'artifact / +')
    assert candidate_artifact_references(f'[file]({href})') == [{'href': href, 'image': False}]


def test_multiple_valid_maps_of_same_source_are_both_checked():
    tables = valid_tables()
    source = observation('source', 'load_wafer_maps')
    maps = [observation(result_id, 'run_python', source_ids=['source'],
        tables=[table_ref(name, rows) for name, rows in tables.items()],
        artifacts=[{'artifact_id': result_id}]) for result_id in ['map-a', 'map-b']]
    stored = {('source', 'wafer_map_dies'): SOURCE_ROWS}
    stored.update({(item['result_id'], name): rows for item in maps for name, rows in tables.items()})
    report = asyncio.run(inspect_wafer_map_contracts(MemoryStore(stored), 'p', 's',
        [source, *maps], selected_result_ids=['map-a', 'map-b']))
    assert report['verdict'] == 'pass'
    assert set(report['result_ids']) == {'source', 'map-a', 'map-b'}


@pytest.mark.parametrize('answer', [
    '<a href="/harness/artifacts/a?session_id=s">file</a>',
    '<img src="/harness/artifacts/a?session_id=s">',
    'MEDIA: /harness/artifacts/a?session_id=s',
    '[file](/harness/artifacts/a?session_id=s',
    '<a href="&#47;harness/artifacts/a?session_id=s">file</a>',
])
def test_frontend_promotable_noncanonical_syntax_fails_closed(answer):
    assert inspect(ArtifactStore(), answer)['verdict'] == 'fail'


@pytest.mark.parametrize('href', [
    '#media:%2Fharness%2Fartifacts%2Fa%3Fsession_id%3Ds',
    '#preview:%2Fharness%2Fartifacts%2Fa%3Fsession_id%3Ds',
    '/a/../harness/artifacts/a?session_id=s',
    '/%68arness/artifacts/a?session_id=s',
    'harness/artifacts/a?session_id=s',
])
def test_browser_aliases_and_encoded_preview_targets_fail_closed(href):
    assert inspect(ArtifactStore(), f'![file]({href})')['verdict'] == 'fail'


def test_artifact_path_in_image_alt_label_is_not_a_second_reference():
    href = artifact_url('s', 'a')
    answer = f'![/harness/artifacts]({href})'
    assert candidate_artifact_references(answer) == [{'href': href, 'image': True}]
    assert inspect(ArtifactStore(), answer)['verdict'] == 'pass'
