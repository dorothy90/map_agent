"""Authenticated REST projections of Hermes 5894e750; PB:416,642,664,700.

No upstream imports, host discovery, configuration or credential reads. Unknown
or unavailable implemented rows retain the transport's handler_not_connected.
"""
from types import SimpleNamespace

from fastapi import HTTPException
from pydantic import TypeAdapter, ValidationError
from starlette.responses import JSONResponse

from .execution import RpcError
from .policy import UPSTREAM
from .session_rpc import NotConnected, SessionRPC


SESSION_LISTS = {'/api/sessions', '/api/profiles/sessions'}
MESSAGES = '/api/sessions/{session_id}/messages'
BOOLEAN = TypeAdapter(bool)


def boolean(query, key, default):
    try:
        return BOOLEAN.validate_python(query.get(key, default))
    except ValidationError:
        raise HTTPException(422, detail=[{'loc': ['query', key],
            'msg': 'Input should be a valid boolean, unable to interpret input',
            'type': 'bool_parsing'}]) from None


def integer(query, key, default, *, minimum=0, maximum=None):
    raw = query.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HTTPException(422, detail=[{'loc': ['query', key],
            'msg': 'Input should be a valid integer, unable to parse string as an integer',
            'type': 'int_parsing'}]) from None
    if value < minimum or maximum is not None and value > maximum:
        bound = minimum if value < minimum else maximum
        lower = value < minimum
        raise HTTPException(422, detail=[{'loc': ['query', key],
            'msg': f'Input should be {"greater than or equal to" if lower else "less than or equal to"} {bound}',
            'type': 'greater_than_equal' if lower else 'less_than_equal',
            'ctx': {'ge' if lower else 'le': bound}}])
    return value


def choice(query, key, default, choices):
    value = query.get(key, default)
    if value not in choices:
        raise HTTPException(400, detail=f'{key} must be one of: {", ".join(v for v in choices if v is not None)}')
    return value


def csv(value):
    return [part.strip() for part in (value or '').split(',') if part.strip()]


async def listing(rpc, query, profile, *, profiles):
    # H/hermes_cli/web_routers/sessions.py:182-246; profiles.py:453-518.
    limit = integer(query, 'limit', 20, maximum=500 if profiles else 100)
    offset = integer(query, 'offset', 0)
    minimum = max(0, integer(query, 'min_messages', 0, minimum=-2**63))
    archived = choice(query, 'archived', 'exclude', ('exclude', 'only', 'include'))
    order = choice(query, 'order', 'recent' if profiles else 'created', ('created', 'recent'))
    # Full config/CWD are not part of the safe projection. No pretend support.
    if boolean(query, 'full', False) or query.get('cwd_prefix'):
        raise NotConnected()
    rows = await rpc.db.list_sessions_rich(limit=-1, compact_rows=True, include_pinned=True,
        order_by_last_active=order == 'recent', include_archived=archived == 'include',
        archived_only=archived == 'only', source=query.get('source') or None,
        sources=csv(query.get('sources')) or None, exclude_sources=csv(query.get('exclude_sources')) or None)
    values = []
    for row in rows:
        value = await rpc.public_list_row(row)
        if value['message_count'] < minimum:
            continue
        value.update(profile=profile, is_default_profile=profile == 'default',
                     archived=bool(row.get('archived')), pinned=bool(row.get('pinned')))
        if 'last_active' in row:
            value['last_active'] = row['last_active']
        # Raw previews/model/config/CWD/usage are deliberately not copied.
        values.append(value)
    counted = [value for value in values if archived == 'include' or
               value['archived'] == (archived == 'only')]
    if profiles:
        values.sort(key=lambda value: (value.get('last_active') if order == 'recent' else None)
                    or value['started_at'] or 0, reverse=True)
        counted = [value for value in values if archived == 'include' or
                   value['archived'] == (archived == 'only')]
    paged = values if profiles and archived != 'only' else counted
    window = paged[offset:offset + limit]
    # H/profiles.py:257-265,506-514; sessions.py:225-229. Pins survive paging.
    candidates = paged[offset + limit:] if profiles else values
    if profiles and archived == 'only':
        candidates += [value for value in values if not value['archived']]
    seen = {value['id'] for value in window}
    window += [value for value in candidates if value['pinned'] and value['id'] not in seen]
    result = {'sessions': window, 'total': len(counted),
              'limit': limit, 'offset': offset, 'storage': {}}
    if profiles:
        result.update(profile_totals={profile: len(counted)}, errors=[])
    return result


async def messages(rpc, sid, query, profile):
    # H/hermes_cli/web_routers/sessions.py:739-785. Ownership precedes tip lookup.
    await rpc.owned(sid)
    sid = await rpc.db.resolve_resume_session_id(sid)
    await rpc.owned(sid)
    limit = integer(query, 'limit', None)
    offset = integer(query, 'offset', 0)
    order = choice(query, 'order', None, (None, 'oldest', 'latest'))
    include_compacted = boolean(query, 'include_compacted', False)
    boolean(query, 'inline_images', True)
    latest = order == 'latest' or (order is None and limit is None)
    page_limit = 500 if limit is None else min(limit, 500)
    history = await rpc.history(sid, include_compacted=include_compacted)
    selected = list(reversed(history)) if latest else history
    selected = selected[offset:offset + page_limit]
    if latest:
        selected.reverse()
    # REST uses content/id for text rows; compact tool rows keep their card fields.
    # Assistant usage/duration must survive this projection so a cold reload
    # can restore the composer token line and turn-duration badge.
    def project_row(row):
        if row['role'] == 'tool':
            return dict(row)
        projected = {'role': row['role'], 'content': row['text']}
        if 'row_id' in row:
            projected['id'] = row['row_id']
        if row['role'] == 'assistant':
            if 'interim' in row:
                projected['interim'] = row['interim']
            if 'usage' in row:
                projected['usage'] = row['usage']
            if 'duration_s' in row:
                projected['duration_s'] = row['duration_s']
        return projected
    projected = [project_row(row) for row in selected]
    return {'session_id': sid, 'profile': profile, 'messages': projected,
            'pagination': {'limit': page_limit, 'offset': offset,
                'order': order or ('latest' if limit is None else 'oldest'), 'returned': len(projected)}}


async def dispatch(services, request, principal, row):
    path = row['path']
    if row['method'] != 'GET':
        raise NotConnected()
    if path == '/api/health':
        # H/web_routers/status.py:119-130: process liveness, not DB/model health.
        return {'ok': True, 'auth_required': True}
    if path == '/api/status':
        # H/web_routers/status.py:383-406,482-547: only the proven auth gate.
        return {'auth_required': True}
    if path == '/api/profiles/active':
        # H/web_routers/profiles.py:885-903; host mapping replaces CLI sticky home.
        profile = services.profile_id_for(principal)
        return {'active': profile, 'current': profile}
    if path not in SESSION_LISTS | {MESSAGES}:
        raise NotConnected()
    profile = services.profile_id_for(principal)
    scope = await services.open(principal)
    rpc = SessionRPC(scope, SimpleNamespace(mode='legacy'))
    if path in SESSION_LISTS:
        return await listing(rpc, request.query_params, profile, profiles=path != '/api/sessions')
    return await messages(rpc, request.path_params['session_id'], request.query_params, profile)


async def serve_http(services, request, principal, row):
    try:
        return JSONResponse(await dispatch(services, request, principal, row))
    except NotConnected:
        return JSONResponse({'error': {'code': 'handler_not_connected',
            'reason': 'handler_not_connected', 'feature': row['path'], 'upstream': UPSTREAM}}, status_code=503)
    except HTTPException as exc:
        return JSONResponse({'detail': exc.detail}, status_code=exc.status_code)
    except RpcError as exc:
        if exc.error.get('message') == 'not_found':
            return JSONResponse({'detail': 'Session not found'}, status_code=404)
        return JSONResponse({'detail': 'Internal server error'}, status_code=500)
    except Exception:
        # Never expose database driver/configuration exception text.
        return JSONResponse({'detail': 'Internal server error'}, status_code=500)
