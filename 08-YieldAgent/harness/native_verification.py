"""Bounded evidence review contract, independent of the upstream engine."""
import json
from jsonschema import validate, ValidationError

REVIEW_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'required': ['verdict', 'issues', 'result_ids'],
    'properties': {
        'verdict': {'type': 'string', 'enum': ['pass', 'fail', 'unknown']},
        'issues': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 20},
        'result_ids': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 50},
    },
}


def unknown(reason):
    return {'verdict': 'unknown', 'issues': [reason], 'result_ids': []}


def final_messages(messages, repair_prompts, answer):
    """Keep tool evidence, replacing internal drafts/feedback with the final answer."""
    clean = []
    for message in messages:
        if message.get('role') == 'user' and message.get('content') in repair_prompts:
            if clean and clean[-1].get('role') == 'assistant' and not clean[-1].get('tool_calls'):
                clean.pop()
            continue
        clean.append(message)
    if clean and clean[-1].get('role') == 'assistant' and not clean[-1].get('tool_calls'):
        clean[-1] = {**clean[-1], 'content': answer}
    else:
        clean.append({'role': 'assistant', 'content': answer})
    return clean


def checked_report(entry, known_ids):
    if entry.get('status') != 'completed' or entry.get('exit_reason') != 'completed' or entry.get('truncated'):
        return unknown('검증 에이전트가 검증을 완료하지 못했습니다.')
    try:
        report = json.loads(entry['summary'])
        validate(report, REVIEW_SCHEMA)
        if not set(report['result_ids']).issubset(known_ids):
            return unknown('검증 결과가 확인되지 않은 자료를 참조했습니다.')
        if report['verdict'] == 'pass' and (report['issues'] or (known_ids and not report['result_ids'])):
            return unknown('검증 통과 판정의 근거가 불충분합니다.')
        if report['verdict'] != 'pass' and not report['issues']:
            return unknown('검증 판정에 사유가 없습니다.')
        return report
    except (ValueError, KeyError, TypeError, ValidationError):
        return unknown('검증 응답이 정해진 형식을 충족하지 못했습니다.')


def verify_and_repair(candidate, review, repair):
    reports = []
    for attempt in range(2):
        if not candidate.get('completed') or candidate.get('failed') or candidate.get('interrupted') or candidate.get('partial') or candidate.get('error'):
            report = unknown('분석 실행이 완료되지 않아 최종 답변을 검증할 수 없습니다.')
        else:
            report = review(candidate)
        reports.append(report)
        if report['verdict'] == 'pass':
            return {**candidate, 'verification': reports}
        if attempt == 0 and report['verdict'] == 'fail':
            candidate = repair(candidate, report)
            continue
        break
    reason = '\n'.join('- ' + issue for issue in report['issues'])
    answer = '최종 검증을 통과하지 못해 답변을 확정하지 못했습니다. 확인된 조회·계산 자료는 아래에 제공됩니다.\n\n' + reason
    return {**candidate, 'completed': False, 'partial': True, 'final_response': answer,
        'turn_exit_reason': 'verification_' + report['verdict'], 'verification': reports}
