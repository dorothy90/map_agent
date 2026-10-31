from harness.native_stop_gate import NativeVerificationGate


def test_fail_continues_once_without_creating_a_new_user_turn():
    reports = iter([{'verdict': 'fail', 'issues': ['scope mismatch'], 'result_ids': ['r']},
                    {'verdict': 'pass', 'issues': [], 'result_ids': ['r']}])
    seen = []
    gate = NativeVerificationGate(lambda candidate: (seen.append(candidate['final_response']), next(reports))[1])
    assert '내부' in gate.evaluate('draft')
    assert gate.evaluate('corrected') is None
    result = gate.finish({'completed': True, 'final_response': 'corrected'})
    assert result['completed'] and seen == ['draft', 'corrected']
    assert len(result['verification']) == 2


def test_failed_or_unreviewed_candidate_never_becomes_final_success():
    gate = NativeVerificationGate(lambda candidate: {'verdict': 'fail', 'issues': ['wrong'], 'result_ids': ['r']})
    assert gate.evaluate('draft')
    assert gate.evaluate('still wrong') is None
    result = gate.finish({'completed': True, 'final_response': 'still wrong'})
    assert result['partial'] and not result['completed']
    assert 'still wrong' not in result['final_response']
    gate = NativeVerificationGate(lambda candidate: {'verdict': 'pass', 'issues': [], 'result_ids': []})
    result = gate.finish({'completed': False, 'failed': True, 'final_response': 'runtime failure'})
    assert not result['completed']


def test_changed_final_after_review_is_not_accepted():
    gate = NativeVerificationGate(lambda candidate: {'verdict': 'pass', 'issues': [], 'result_ids': []})
    gate.evaluate('verified')
    result = gate.finish({'completed': True, 'final_response': 'changed'})
    assert result['partial'] and not result['completed']


def test_installed_gate_keeps_upstream_user_identity_and_removes_scaffolding(tmp_path):
    import os
    from pathlib import Path
    import subprocess
    import sys

    harness = Path(__file__).resolve().parents[1]
    code = '''
import sys
from types import SimpleNamespace
sys.path[:0] = [sys.argv[1] + '/engine', sys.argv[1]]
from agent import turn_final_response
from agent.conversation_compression import _is_real_user_message
from agent.context_compressor import ContextCompressor
from agent.turn_context import export_current_turn_boundary
from agent.turn_finalizer import _drop_verification_continuation_scaffolding
from agent.session_persistence import _is_ephemeral_scaffolding
from native_stop_gate import NativeVerificationGate
from native_verification import final_messages
root = SimpleNamespace(_current_turn_id='original-turn', _user_turn_count=1)
child = SimpleNamespace()
gate = NativeVerificationGate(lambda c: {'verdict': 'fail', 'issues': ['scope'], 'result_ids': []})
original = turn_final_response.apply_stop_gates
restore = gate.install(root)
messages = [{'role': 'user', 'content': 'original question'}]
state = dict(final_response='draft', messages=messages, conversation_history=[],
    pending_verification_response=None, pending_verification_response_previewed=False)
try:
    child_verdict = turn_final_response.apply_stop_gates(child, {'role': 'assistant', 'content': 'draft'}, **state)
    assert not gate.reports and not child_verdict.continue_turn
    verdict = turn_final_response.apply_stop_gates(root, {'role': 'assistant', 'content': 'draft'}, **state)
    assert verdict.continue_turn and root._current_turn_id == 'original-turn' and root._user_turn_count == 1
    nudge = messages[-1]
    assert all(_is_ephemeral_scaffolding(row) for row in messages[1:])
    assert not _is_real_user_message(nudge)
    assert not ContextCompressor._is_real_user_turn(nudge)
    assert 'scope' not in ContextCompressor._derive_auto_focus_topic(messages)
    assert 'original question' in ContextCompressor._latest_user_task_snapshot(messages)
    _drop_verification_continuation_scaffolding(messages)
    assert messages == [{'role': 'user', 'content': 'original question'}]
    messages.append({'role': 'assistant', 'content': 'corrected'})
    result = export_current_turn_boundary(root, {'messages': messages}, 'original question')
    clean = final_messages(messages, [], 'verified', turn_id=result['turn_id'],
        current_turn_user_idx=result['current_turn_user_idx'], conversation_history=[], user_message='original question')
    assert clean[-1]['content'] == 'verified' and len(clean) == 2
finally:
    restore()
assert turn_final_response.apply_stop_gates is original
'''
    env = {**os.environ, 'HERMES_HOME': str(tmp_path / 'profile'),
        'HERMES_DISABLE_LAZY_INSTALLS': '1'}
    result = subprocess.run([sys.executable, '-B', '-c', code, str(harness)],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
