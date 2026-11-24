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
