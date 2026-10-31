"""Publication boundary with fake worker/store and real executor publication."""
import asyncio
import hashlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from harness.config import Settings
from harness.tools.registry import ToolRegistry
from harness.types import ToolObservation


CASES = [
    (['pass'], {}, 'second', 'completed', None),
    (['fail', 'pass'], {}, 'second', 'completed', None),
    (['fail', 'fail'], {}, 'second', 'partial', 'fail'),
    (['unknown'], {}, 'second', 'partial', 'unknown'),
    (['pass'], {}, 'different', 'partial', 'unknown'),
    ([], {}, 'second', 'partial', 'unknown'),
    (['pass'], {'interrupted': True}, 'second', 'partial', 'unknown'),
    (['pass'], {'error': 'worker error'}, 'second', 'failed', 'unknown'),
    (['pass'], {'cancelled': True}, 'second', 'partial', 'unknown'),
    (['pass'], {'partial': True}, 'second', 'partial', 'unknown'),
    ([], {'failed': True, 'turn_exit_reason': 'worker_failed'}, 'second', 'failed', 'worker_failed'),
    ([], {'partial': True, 'error': 'compression deferred', 'turn_exit_reason': 'compression_timeout'},
     'second', 'partial', 'compression_timeout'),
    *[([], {'completed': False, 'partial': True, 'turn_exit_reason': reason}, 'second', 'partial', reason)
      for reason in ('tokens_limit', 'models_limit')],
    *[(['pass'], {flag: True, 'turn_exit_reason': 'worker_stopped'}, 'second', 'partial', 'worker_stopped')
      for flag in ('interrupted', 'cancelled', 'canceled', 'cancel')],
    ([], {'turn_exit_reason': 'completed'}, 'second', 'partial', 'unknown'),
    (['pass'], {'turn_exit_reason': 'hermes_returned'}, 'different', 'partial', 'unknown'),
    (['unknown'], {'turn_exit_reason': 'completed'}, 'second', 'partial', 'unknown'),
]


@pytest.mark.parametrize('verdicts,flags,final,status,reason', CASES)
def test_native_publication_boundary(monkeypatch, tmp_path, verdicts, flags, final, status, reason):
    import harness.native_hermes as native
    from harness.completion import render_results
    from harness.control import RunController
    from harness.store import HarnessStore
    monkeypatch.setattr(native, 'ROOT', tmp_path)
    candidates = ['REJECTED_PRIVATE_DRAFT', 'FINAL_PRIVATE_답변']
    final_text = candidates[1] if final == 'second' else 'MISMATCH_PRIVATE_DRAFT'
    events, updates, saved = [], [], []
    evidence = ToolObservation(principal_id='p', session_id='s', run_id='r',
        invocation_id='read', result_id='evidence', tool_name='read', status='success',
        summary='조회 완료', preview_rows=[{'value': 'TABLE_CELL'}], total_rows=1,
        artifact_refs=[{'artifact_id': 'artifact', 'title': '자료', 'mime': 'text/html'}])
    table = render_results([evidence.model_dump(mode='json')])
    async def noop(*a, **kw): pass
    class Store:
        def __init__(self): self.runs = self
        async def update_one(self, query, update): updates.append(deepcopy(update))
        async def event(self, run_id, kind, payload, **kw):
            events.append({'type': kind, 'payload': deepcopy(payload)})
        async def put_result(self, principal, session, metadata, rows, artifacts):
            saved.append(deepcopy(metadata))
            return ToolObservation(principal_id=principal, session_id=session,
                result_id='result-' + metadata['invocation_id'], **metadata)
        async def find_one_and_update(self, query, pipeline, **kw):
            fields = pipeline[0]['$set']
            event = fields['events']['$concatArrays'][1][0]
            events.append({'type': event['type']['$literal'], 'payload': event['payload']['$literal']})
            return {'status': fields['status']['$literal']}
    store = Store()
    original_bridge = native.NativeBridge
    class Bridge(original_bridge):
        def __init__(self, registry, context):
            super().__init__(registry, context)
            self.observations.append(evidence.model_dump(mode='json'))
    monkeypatch.setattr(native, 'NativeBridge', Bridge)
    lines = [{'type': 'commentary', 'content': '숨길 진행 설명'}, {'type': 'step', 'tokens': 1, 'models': 1}]
    for index, verdict in enumerate(verdicts):
        candidate = candidates[0] if len(verdicts) > 1 and index == 0 else candidates[1]
        lines.extend([
            {'type': 'call', 'method': 'verification_start', 'call_id': f'call-s{index}',
             'review_id': f'v{index}', 'attempt': index + 1, 'candidate': candidate},
            {'type': 'call', 'method': 'verification_finished', 'call_id': f'call-f{index}',
             'review_id': f'v{index}', 'elapsed': .1,
             'report': {'verdict': verdict, 'issues': [] if verdict == 'pass' else ['근거 부족'], 'result_ids': []}},
        ])
    # A worker-supplied pass alone must never authorize publication.
    lines.append({'type': 'result', 'result': {'completed': True, 'final_response': final_text,
        'verification': [{'verdict': 'pass', 'issues': [], 'result_ids': []}], **flags}})
    messages = iter(lines)
    async def readline():
        message = next(messages, None)
        return (json.dumps(message) + '\n').encode() if message else b''
    process = SimpleNamespace(returncode=0, stdout=SimpleNamespace(readline=readline),
        stdin=SimpleNamespace(write=lambda data: None, drain=noop))
    async def spawn(*a, **kw): return process
    monkeypatch.setattr(native.asyncio, 'create_subprocess_exec', spawn)
    run = dict(principal_id='p', session_id='s', run_id='r', epoch=1, query='q')
    context = SimpleNamespace(settings=Settings(), run=run, remaining_seconds=lambda: 10, check=noop, store=store)
    async def scenario():
        # Tool evidence keeps the existing artifact event, independently of answers.
        await original_bridge(ToolRegistry(), context).executor.publish(evidence, run)
        result = await native.run_native(context, ToolRegistry())
        wire = json.dumps([events, saved, updates], ensure_ascii=False)
        for candidate in [*candidates, final_text]: assert candidate not in wire
        assert table not in wire and not any(e['type'] == 'run_finished' for e in events)
        assert result['status'] == status
        if status == 'completed':
            assert result['answer_text'] == candidates[1]
        else:
            assert result['stop_reason'] == ('verification_' + reason if reason in ('fail', 'unknown') else reason)
            assert result['answer_text'].startswith('최종 검증을 통과하지 못해 답변을 확정하지 못했습니다.')
            for candidate in [*candidates, final_text]: assert candidate not in result['answer']
        assert candidates[0] not in json.dumps(result, ensure_ascii=False)
        assert table not in result['answer_text'] and result['answer'].endswith(table)
        assert len(saved) == len(verdicts)
        for index, observation in enumerate(saved):
            candidate = candidates[0] if len(verdicts) > 1 and index == 0 else candidates[1]
            assert observation['validated_arguments'] == {'attempt': index + 1,
                'candidate_sha256': hashlib.sha256(candidate.encode('utf-8')).hexdigest(), 'candidate_chars': len(candidate)}
        controller = RunController(store, context.settings, None)
        await controller._save_context(run, result)
        await HarnessStore.finish(store, 'r', 1, result['status'], result['answer'], result['stop_reason'])
        finished = [e for e in events if e['type'] == 'run_finished']
        assert len(finished) == 1 and finished[0]['payload']['answer'] == result['answer']
        assert [e['type'] for e in events].count('artifact') == 1
        assert not any(e['type'] == 'commentary' for e in events)
        assert all('TABLE_CELL' not in json.dumps(e) for e in events[:-1])
        # Public history/replay uses the saved answer and the same terminal event.
        from harness.router import public_run
        public = public_run({**run, **result})
        assert public['answer'] == finished[0]['payload']['answer']
        assert candidates[0] not in json.dumps([public, events], ensure_ascii=False)
    asyncio.run(scenario())
