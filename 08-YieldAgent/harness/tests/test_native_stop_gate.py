from harness.native_stop_gate import NativeVerificationGate


def test_fail_continues_once_without_creating_a_new_user_turn():
    reports = iter([{'verdict': 'fail', 'issues': ['scope mismatch'], 'result_ids': ['r']},
                    {'verdict': 'pass', 'issues': [], 'result_ids': ['r']}])
    seen = []
    gate = NativeVerificationGate(lambda candidate: (seen.append(candidate['final_response']), next(reports))[1])
    nudge = gate.evaluate('draft')
    assert '내부' in nudge and '한계' in nudge
    assert gate.evaluate('corrected') is None
    result = gate.finish({'completed': True, 'final_response': 'corrected'})
    assert result['completed'] and seen == ['draft', 'corrected']
    assert len(result['verification']) == 2


def test_failed_candidate_preserves_latest_answer_with_last_issues():
    gate = NativeVerificationGate(lambda candidate: {'verdict': 'fail', 'issues': ['wrong'], 'result_ids': ['r']})
    assert gate.evaluate('draft')
    assert gate.evaluate('still wrong') is None
    result = gate.finish({'completed': True, 'final_response': 'still wrong'})
    assert result['partial'] and not result['completed']
    assert result['verification_candidate'] == 'still wrong'
    assert result['final_response'].startswith('still wrong\n\n---\n⚠️')
    assert '- wrong' in result['final_response'] and 'draft' not in result['final_response']


def test_unreviewed_or_unusable_candidate_keeps_fixed_fallback():
    gate = NativeVerificationGate(lambda candidate: {'verdict': 'pass', 'issues': [], 'result_ids': []})
    result = gate.finish({'completed': False, 'failed': True, 'final_response': 'runtime failure'})
    assert not result['completed'] and 'runtime failure' not in result['final_response']
    assert result['final_response'].startswith('최종 검증을 통과하지 못해')

    gate = NativeVerificationGate(lambda candidate: {'verdict': 'fail', 'issues': ['wrong'], 'result_ids': []})
    gate.evaluate('reviewed')
    for final_response in ('different', '', '   '):
        result = gate.finish({'completed': True, 'final_response': final_response})
        assert result['final_response'].startswith('최종 검증을 통과하지 못해')
        assert 'verification_candidate' not in result


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


def test_repair_feedback_explains_named_evidence_and_preserves_unknown():
    gate = NativeVerificationGate(lambda candidate: {
        'verdict': 'fail', 'issues': ['wafer_map_manifest 필드 누락'], 'result_ids': ['plot']})
    nudge = gate.evaluate('draft')
    assert "emit_table(manifest_df, name='wafer_map_manifest')" in nudge
    assert "emit_table(members_df, name='wafer_map_members')" in nudge
    assert "emit_table(cells_df, name='wafer_map_cells')" in nudge
    assert 'wafer_map_manifest 필드 누락' in nudge
    gate = NativeVerificationGate(lambda candidate: {
        'verdict': 'unknown', 'issues': ['검증 예산 부족'], 'result_ids': []})
    assert gate.evaluate('draft') is None
    result = gate.finish({'completed': True, 'final_response': 'draft'})
    assert result['partial'] and not result['completed']
    assert '검증 예산 부족' in result['final_response']
