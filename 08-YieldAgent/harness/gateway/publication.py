"""Pure host publication policy (PB:523–531 B, PB:662; no upstream equivalent)."""
import hashlib

try:
    from ..native_verification import verification_failure_answer
except ImportError:  # Standalone worker import.
    from native_verification import verification_failure_answer


def candidate_digest(text):
    return {'candidate_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(),
        'candidate_chars': len(text)}


def commentary_event(settings, content):
    return {'content': content} if getattr(settings, 'publish_commentary', False) is True else None


def settle(result, reports, verified_candidates):
    """Reports and aligned candidate digests come from host-observed review RPCs."""
    stopped = any(result.get(key) for key in
        ('failed', 'interrupted', 'error', 'cancelled', 'canceled', 'cancel'))
    complete = result.get('completed') and not result.get('partial') and not stopped
    report = reports[-1] if reports else None
    answer = result.get('final_response')
    matches = (len(verified_candidates) == len(reports) and bool(verified_candidates)
        and isinstance(answer, str)
        and verified_candidates[-1] == candidate_digest(answer)['candidate_sha256'])
    if complete and report and report['verdict'] == 'pass' and matches:
        return {'status': 'completed', 'stop_reason': result.get('turn_exit_reason') or 'hermes_returned',
            'answer_text': answer}
    if complete and report and report['verdict'] in ('fail', 'unknown') and matches:
        return {'status': 'partial', 'stop_reason': 'verification_' + report['verdict'],
            'answer_text': verification_failure_answer(answer, report)}
    candidate = result.get('verification_candidate')
    gate_partial = (not stopped and result.get('completed') is False and result.get('partial') is True
        and report and report['verdict'] in ('fail', 'unknown')
        and result.get('turn_exit_reason') == 'verification_' + report['verdict']
        and len(verified_candidates) == len(reports) and bool(verified_candidates)
        and isinstance(candidate, str) and candidate.strip()
        and verified_candidates[-1] == candidate_digest(candidate)['candidate_sha256']
        and answer == verification_failure_answer(candidate, report))
    if gate_partial:
        return {'status': 'partial', 'stop_reason': 'verification_' + report['verdict'], 'answer_text': answer}
    # Match result_status(): partial/interrupted take precedence over error,
    # but an explicit failure always remains failed.
    failed = result.get('failed') or (result.get('error')
        and not (result.get('partial') or result.get('interrupted')))
    worker_reason = result.get('turn_exit_reason')
    preserve_reason = worker_reason and (stopped or result.get('partial') or (
        not worker_reason.startswith('verification_')
        and worker_reason not in ('completed', 'hermes_returned')))
    if stopped or not report or report['verdict'] == 'pass' or (complete and not matches):
        report = {'verdict': 'unknown',
            'issues': ['실행이 완료되지 않았거나 최종 답변이 검증한 답변과 다릅니다.']}
    return {'status': 'failed' if failed else 'partial',
        'stop_reason': worker_reason if preserve_reason else 'verification_' + report['verdict'],
        'answer_text': verification_failure_answer(None, report)}
