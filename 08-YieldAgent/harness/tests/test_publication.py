from copy import deepcopy
import hashlib
from types import SimpleNamespace

import pytest


def test_digest_and_commentary():
    from harness.gateway.publication import candidate_digest, commentary_event
    text = '검증할 답변 🐣'
    assert candidate_digest(text) == {'candidate_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'candidate_chars': len(text)}
    for setting in (SimpleNamespace(), SimpleNamespace(publish_commentary=False)):
        assert commentary_event(setting, text) is None
    assert commentary_event(SimpleNamespace(publish_commentary=True), text) == {'content': text}


@pytest.mark.parametrize('flags,verdicts,match,expected', [
    ({}, ['pass'], True, 'completed'),
    ({}, ['fail', 'pass'], True, 'completed'),
    ({}, ['fail', 'fail'], True, 'verification_fail'),
    ({'completed': False, 'partial': True}, ['fail', 'fail'], False, 'verification_fail'),
    ({}, ['unknown'], True, 'verification_unknown'),
    ({}, ['pass'], False, 'verification_unknown'),
    ({}, ['fail'], False, 'verification_unknown'),
    ({}, [], True, 'verification_unknown'),
    ({'completed': False}, ['pass'], True, 'verification_unknown'),
    *[({flag: True}, ['pass'], True, 'verification_unknown')
      for flag in ('failed', 'partial', 'interrupted', 'error', 'cancelled', 'canceled', 'cancel')],
])
def test_settle_matrix_is_pure(flags, verdicts, match, expected):
    from harness.gateway.publication import candidate_digest, settle
    text = 'PRIVATE_CANDIDATE'
    result = {'completed': True, 'final_response': text, **flags}
    reports = [{'verdict': v, 'issues': [] if v == 'pass' else ['근거 부족']} for v in verdicts]
    digests = [candidate_digest(text if match else 'different')['candidate_sha256'] for _ in reports]
    inputs = deepcopy((result, reports, digests))
    decision = settle(result, reports, digests)
    assert (result, reports, digests) == inputs
    if expected == 'completed':
        assert decision['status'] == 'completed' and decision['answer_text'] == text
    else:
        status = 'failed' if flags.get('failed') or flags.get('error') else 'partial'
        assert decision['status'] == status and decision['stop_reason'] == expected
        assert text not in str(decision)
        assert decision['answer_text'].startswith('최종 검증을 통과하지 못해 답변을 확정하지 못했습니다.')
        if expected == 'verification_fail': assert '- 근거 부족' in decision['answer_text']


def test_pass_requires_host_candidate_record():
    from harness.gateway.publication import settle
    assert settle({'completed': True, 'final_response': 'private'},
        [{'verdict': 'pass', 'issues': []}], [])['status'] == 'partial'


@pytest.mark.parametrize('flags,status', [
    ({'failed': True}, 'failed'),
    ({'error': 'worker error'}, 'failed'),
    ({'error': 'compression deferred', 'partial': True}, 'partial'),
    *[({flag: True}, 'partial') for flag in
      ('partial', 'interrupted', 'cancelled', 'canceled', 'cancel')],
    ({}, 'partial'),
])
@pytest.mark.parametrize('exit_reason', ['tokens_limit', 'models_limit', 'custom_worker_stop'])
def test_nonpublic_preserves_worker_status_and_exit_reason(flags, status, exit_reason):
    from harness.gateway.publication import settle
    result = {'completed': True, 'final_response': 'PRIVATE_CANDIDATE',
        'turn_exit_reason': exit_reason, **flags}
    decision = settle(result, [], [])
    assert decision['status'] == status
    assert decision['stop_reason'] == exit_reason
    assert 'PRIVATE_CANDIDATE' not in str(decision)
    assert decision['answer_text'].startswith('최종 검증을 통과하지 못해 답변을 확정하지 못했습니다.')


@pytest.mark.parametrize('exit_reason', ['completed', 'hermes_returned', 'verification_pass'])
def test_host_downgrade_replaces_success_reason(exit_reason):
    from harness.gateway.publication import settle
    decision = settle({'completed': True, 'final_response': 'PRIVATE_CANDIDATE',
        'turn_exit_reason': exit_reason}, [], [])
    assert decision['status'] == 'partial'
    assert decision['stop_reason'] == 'verification_unknown'
    assert 'PRIVATE_CANDIDATE' not in str(decision)


def test_worker_verification_reason_survives_missing_host_report():
    from harness.gateway.publication import settle
    decision = settle({'partial': True, 'turn_exit_reason': 'verification_fail',
        'final_response': 'PRIVATE_CANDIDATE'}, [], [])
    assert decision['stop_reason'] == 'verification_fail'
    assert 'PRIVATE_CANDIDATE' not in str(decision)
