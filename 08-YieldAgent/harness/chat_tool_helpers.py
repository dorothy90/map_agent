"""Provider and tool bindings for a stock Hermes web chat worker."""
import hashlib
import json
import uuid
from functools import wraps


def engine_provider(base_url):
    from agent.model_metadata import _infer_provider_from_url
    return _infer_provider_from_url(base_url) or 'custom'


def constrain_session_search(search_module):
    def profile_local_db(profile):
        if profile is None or not str(profile).strip():
            return None
        raise ValueError('cross-profile session search is disabled')
    search_module._resolve_profile_db = profile_local_db

    search = search_module.session_search
    if getattr(search, '_yield_scope_constrained', False):
        return

    @wraps(search)
    def constrained(*args, **kwargs):
        result = search(*args, **kwargs)
        try:
            payload = json.loads(result)
        except (TypeError, ValueError):
            return result
        if isinstance(payload, dict) and payload.get('success') is True:
            payload['scope_confirmation'] = {
                'source': 'session_search',
                'confirms_current_scope': False,
                'note': '검색 결과 자체는 현재 요청의 조건을 확정하지 않는다. 현재 대화에서 이미 사용자 확인된 조건은 유지한다. 다른 세션 또는 현재 대화에서 미확정인 조건은 후보이며, 조회·맵·분석 도구에 사용하기 전에 clarify로 확인한다.',
            }
        return json.dumps(payload, ensure_ascii=False)

    constrained._yield_scope_constrained = True
    search_module.session_search = constrained


def observe_skill_reads(skills_tool, emit):
    """Hermes uses skill_view for both model tool calls and configured auto-loads."""
    view = skills_tool.skill_view

    @wraps(view)
    def observed(*args, **kwargs):
        result = view(*args, **kwargs)
        payload = json.loads(result)
        if payload.get('success') and not payload.get('is_binary') and isinstance(payload.get('content'), str):
            emit({'type': 'skill_read', 'name': payload['name'], 'resource': payload.get('file') or 'SKILL.md',
                  'sha256': hashlib.sha256(payload['content'].encode('utf-8')).hexdigest()})
            if kwargs.get('preprocess') is False:
                emit({'type': 'skill_result', 'invocation_id': 'preload-' + uuid.uuid4().hex,
                      'name': 'skill_view', 'args': {'name': payload['name']}, 'result': result})
        return result

    skills_tool.skill_view = observed
