"""Offline worker RPC exercises the host's actual artifact verification branch."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from harness.artifact_delivery import ARTIFACT_DELIVERY, artifact_url
from harness.config import Settings
from harness.tools.registry import ToolRegistry
from harness.types import ToolObservation


@pytest.mark.parametrize('repair', [False, True])
def test_host_artifact_gate_catalog_and_repaired_publication(monkeypatch, tmp_path, repair):
    import harness.native_hermes as native
    from harness.gateway.events import verified_answer_with_artifacts
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    refs = [{'artifact_id': f'artifact-{n}', 'result_id': 'current',
        'title': f'Output {n}', 'mime': 'image/png'} for n in range(30)]
    current = ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='current', result_id='current', tool_name='read', artifact_refs=refs)
    prior_ref = {'artifact_id': 'prior-file', 'result_id': 'prior', 'title': 'Prior', 'mime': 'text/html'}
    prior = ToolObservation(principal_id='p', session_id='s', run_id='earlier',
        invocation_id='prior', result_id='prior', tool_name='read', artifact_refs=[prior_ref])
    noisy = [ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id=f'noise-{n}', result_id=f'noise-{n}', tool_name='read',
        summary='Summary ' * 200, validated_arguments={'input': 'x' * 1000},
        preview_rows=[{'value': 'x' * 1000}] * 5) for n in range(80)]
    replies, saved = [], []
    async def noop(*args, **kwargs): pass
    class Store:
        def __init__(self): self.runs = self
        update_one = noop
        event = noop
        async def artifact(self, principal, session, artifact_id):
            assert (principal, session) == ('p', 's')
            ref = next((ref for ref in [*refs, prior_ref] if ref['artifact_id'] == artifact_id), None)
            if ref is None: raise PermissionError('Unavailable')
            return ref, b'blob'
        async def observation(self, principal, session, result_id):
            assert (principal, session) == ('p', 's')
            return {'current': current, 'prior': prior}[result_id]
        async def put_result(self, principal, session, metadata, rows, artifacts):
            observation = ToolObservation(principal_id=principal, session_id=session,
                result_id='result-' + metadata['invocation_id'], **metadata)
            saved.append(observation.model_dump(mode='json'))
            return observation
    store = Store()
    original_bridge = native.NativeBridge
    class Bridge(original_bridge):
        def __init__(self, registry, context):
            super().__init__(registry, context)
            self.observations = [item.model_dump(mode='json') for item in [current, *noisy]]
    monkeypatch.setattr(native, 'NativeBridge', Bridge)
    prior_link = f'[Prior]({artifact_url("s", "prior-file")})'
    invalid = f'[Missing]({artifact_url("s", "missing")})\n\n{prior_link}'
    valid = f'Only the selected final output.\n\n![Final]({artifact_url("s", "artifact-29")})\n\n{prior_link}'
    candidates = [invalid, valid] if repair else [invalid]
    lines = []
    for index, candidate in enumerate(candidates):
        lines.extend([
            {'type': 'call', 'method': 'verification_start', 'call_id': f'call-s{index}',
             'review_id': f'v{index}', 'attempt': index + 1, 'candidate': candidate},
            {'type': 'call', 'method': 'verification_finished', 'call_id': f'call-f{index}',
             'review_id': f'v{index}', 'elapsed': .1,
             'report': {'verdict': 'pass', 'issues': [], 'result_ids': ['prior', 'current']}},
        ])
    lines.append({'type': 'result', 'result': {'completed': True, 'final_response': candidates[-1]}})
    messages = iter(lines)
    async def readline():
        message = next(messages, None)
        return (json.dumps(message) + '\n').encode() if message else b''
    process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(readline=readline),
        stdin=SimpleNamespace(write=lambda data: replies.append(json.loads(data)), drain=noop))
    async def spawn(*args, **kwargs): return process
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    run = dict(principal_id='p', session_id='s', run_id='r', epoch=1, query='q',
        artifact_delivery=ARTIFACT_DELIVERY)
    context = SimpleNamespace(settings=Settings(), run=run, remaining_seconds=lambda: 20,
        check=noop, store=store)
    result = asyncio.run(native.run_native(context, ToolRegistry()))
    bundles = [message['result'] for message in replies if str(message.get('call_id', '')).startswith('call-s')]
    reports = [message['result']['report'] for message in replies if str(message.get('call_id', '')).startswith('call-f')]
    assert len(bundles) == len(candidates)
    for bundle in bundles:
        assert len(json.dumps(bundle['evidence'], ensure_ascii=False)) <= 20_000
        assert len(bundle['artifact_catalog']) == 31
        assert {item['artifact_id'] for item in bundle['artifact_catalog']} == {ref['artifact_id'] for ref in [*refs, prior_ref]}
        assert 'prior' in bundle['known_result_ids']
        assert bundle['artifact_reference_review']['selected_result_ids'][-1] == 'prior'
    assert reports[0]['verdict'] == 'fail'
    assert saved[0]['scope']['verdict'] == 'fail'
    if repair:
        assert reports[1]['verdict'] == 'pass'
        assert result['status'] == 'completed'
        assert result['answer_text'] == valid
        projected = verified_answer_with_artifacts({**run, **result, 'active': False})
        assert projected == valid
        assert 'artifact-0?' not in projected
    else:
        assert result['status'] == 'partial'
        assert result['stop_reason'] == 'verification_fail'
