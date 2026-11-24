"""M1 policy: PORTING_BASELINE §3.1–3.4, §7.1–7.2 and plan §1.3–1.6.

`implemented` means the M1 implementation lane, NOT a connected PR-1 handler.
Exact exceptions take priority over route-family defaults. Unknown RPC rules
raise rather than silently inheriting a classification. No policy executes code.
"""
from .rpc_rules import RPC_RULES

UPSTREAM = '5894e750'
UPSTREAM_COMMIT = '5894e750c3dba980d43b25aa29e3958149ba2c61'
STATUSES = ('implemented', 'stub', 'unsupported')


def expand(pattern):
    if '{' not in pattern:
        return {pattern}
    prefix, rest = pattern.split('{', 1)
    choices, suffix = rest.split('}', 1)
    return {prefix + choice + suffix for choice in choices.split(',')}


def decision(status, feature, detail, evidence, reason=None):
    return {'classification': status, 'feature': feature,
            'reason': reason or {'implemented': 'm1_implementation_target',
                                 'stub': 'deferred_milestone',
                                 'unsupported': 'disabled_by_policy'}[status],
            'detail': detail, 'policy_source': evidence}


def rpc_policy(name, mode):
    matches = [row for row in RPC_RULES if row[0] == mode
               and any(name in expand(pattern) for pattern in row[1])]
    if len(matches) != 1:
        raise ValueError(f'{mode}:{name}: expected one policy rule, got {len(matches)}')
    _, _, status, detail, evidence = matches[0]
    result = decision(status, name, detail, evidence)
    if mode == 'authority' and name in {'worker.adopt', 'worker.persist', 'worker.register'}:
        result['reason'] = 'external_worker_control_not_needed'
    elif name == 'session.save':
        result['reason'] = 'local_file_save_disabled'
    elif name == 'session.export':
        result['reason'] = 'server_download_deferred'
    # Action-level restrictions are separate from the name-level classification.
    if name.startswith('mcp.') or name == 'reload.mcp':
        result.update(reason='deferred_milestone_3', denied_actions=['stdio', 'SSE'])
    elif name.startswith(('skills.', 'profiles.')) and status == 'stub':
        result['reason'] = 'deferred_milestone_3'
    elif name.startswith('cron.'):
        result['reason'] = 'deferred_milestone_4'
    if name == 'plugins.manage':
        result['denied_actions'] = ['code_install', 'code_update', 'code_execute']
    if name == 'prompt.submit':
        result['denied_actions'] = ['steer', 'redirect', 'queue_edit']
    if name in {'config.get', 'model.options', 'profiles.list', 'tools.list', 'tools.show', 'toolsets.list'}:
        result['projection'] = 'principal_owned_server_allowlisted_no_secrets'
    return result


# Event exceptions implement B안: retention of a type never authorizes publication.
EVENT_INTERNAL = {'message.delta', 'message.interim'}
EVENT_STATUS_ONLY = {'reasoning.available', 'reasoning.delta', 'thinking.delta', 'agent.print'}
EVENT_IMPLEMENTED = set('''message.start message.complete tool.start tool.complete
    tool.generating tool.progress tool.output_risk status.update error gateway.ready
    session.info session.resume_progress session.reclaimed session.title session.usage
    sessions.changed approval.request approval.settled clarify.request clarify.settled
    approval.cancelled request.cancel session.replay_gap session.updated agent.step
    notice notification.clear notification.show todo.updated'''.split())
EVENT_DEFERRED = set('''btw.complete cron.changed message.reaction reaction
    session.control.update session.tools_changed skills.changed skin.changed review.summary'''.split())
EVENT_DISABLED_FAMILIES = set('''agent background billing bot_relay browser connection
    display free_tier moa onboarding pairing pane pet platforms preview projects setup
    subagent terminal tip voice wake'''.split())


def event_policy(name):
    publication = 'public_projection'
    if name in EVENT_INTERNAL:
        status, publication = 'implemented', 'internal_only'
    elif name in EVENT_STATUS_ONLY:
        status, publication = 'implemented', 'status_only'
    elif name in EVENT_IMPLEMENTED:
        status = 'implemented'
        if name == 'message.complete':
            publication = 'verified_final_only'
    elif name in EVENT_DEFERRED:
        status, publication = 'stub', 'producer_disabled'
    elif name.split('.')[0] in EVENT_DISABLED_FAMILIES:
        status, publication = 'unsupported', 'producer_disabled'
    else:
        raise ValueError(f'Unclassified event: {name}')
    return {**decision(status, name, 'Type retained; publication constrained by M1/B안.',
                        'PORTING_BASELINE:7.1; m1_plan_codex.log:1.5'),
            'publication': publication}


def request_policy(name):
    status = ('implemented' if name in {'approval', 'clarify'} else
              'stub' if name in {'theme.apply', 'theme.pick'} else 'unsupported')
    return {**decision(status, name, 'D05 pending controls; theme deferred; host bridges denied.',
                        'PORTING_BASELINE:7.1 server requests'),
            'decline': None if status == 'implemented' else 'jsonrpc_method_not_found'}


# HTTP defaults below apply only to these statically reviewed router files.
HTTP_DEFAULTS = {
    **dict.fromkeys(('actions', 'audio', 'chat_workspaces', 'delegation', 'display',
                    'files', 'git', 'local_models', 'messaging', 'oauth', 'shared_metrics',
                    'status', 'config_env', 'models', 'tools', 'ops'), 'unsupported'),
    **dict.fromkeys(('analytics', 'dashboard_ui', 'cron', 'mcp', 'memory_providers',
                    'skills', 'profiles', 'sessions'), 'stub'),
}
HTTP_IMPLEMENTED = set('''/api/sessions /api/sessions/{session_id}/messages
    /api/health /api/host/identity /api/health/idle /api/status
    /api/config /api/config/defaults /api/config/schema /api/egress/status
    /api/model/info /api/model/options /api/model/recommended-default
    /api/tools/toolsets /api/tools/toolsets/{name}/config /api/tools/toolsets/{name}/models
    /api/profiles /api/profiles/active /api/profiles/sessions /api/profiles/sessions/sidebar'''.split())
HTTP_DENIED_PATHS = set('''/api/config/raw /api/mcp/catalog/install
    /api/skills/hub/install /api/skills/hub/uninstall /api/skills/hub/update
    /api/profiles/projects/tree /api/profiles/sessions/pull-requests
    /api/profiles/{name}/setup-command /api/profiles/{name}/open-terminal
    /api/profiles/{name}/model /api/profiles/{name}/export /api/profiles/import
    /api/profiles/{name}/desktop-overlay /api/dashboard/plugins/rescan
    /api/dashboard/plugin-providers'''.split())
# Approved PR-1 exceptions match the reviewed method and path exactly.
HTTP_DENIED_ROUTES = {
    ('POST', path): ('cross_user_session_management',
                     '다른 사용자 세션에 영향 가능한 운영자 기능 제외')
    for path in ('/api/sessions/import', '/api/sessions/owner-backfill',
                 '/api/sessions/prune', '/api/sessions/bulk-delete')
}
HTTP_DENIED_ROUTES[('POST', '/api/memory/reset')] = (
    'global_memory_reset_before_user_isolation',
    '사용자별 메모리 격리 설계 전 전역 초기화 위험으로 제외')
HTTP_DENIED_PREFIXES = ('/dashboard-plugins/', '/api/dashboard/agent-plugins/')


def route_policy(path, method, file, external=False):
    evidence = 'PORTING_BASELINE:3.3,7.1; m1_plan_codex.log:1.6'
    if (method, path) in {('GET', '/api/fs/download'), ('HEAD', '/api/fs/download'),
                          ('POST', '/api/files/ticket')}:
        return decision('implemented', path, 'Registered generated output files only.',
                        'Hermes 9ef4800b browser files; Y-Agent run output scope')
    if method == 'WEBSOCKET':
        status = 'implemented' if path == '/api/ws' else 'unsupported'
    elif external:
        # Dashboard login is not Yield authentication. Only identity/ticket/logout
        # contracts are targets for the PR-2 platform-auth adapter, never native tokens.
        status = 'implemented' if path in {'/api/auth/me', '/api/auth/ws-ticket', '/auth/logout'} else 'unsupported'
    elif file not in HTTP_DEFAULTS:
        raise ValueError(f'Unreviewed router: {file}')
    elif (method, path) in HTTP_DENIED_ROUTES:
        status = 'unsupported'
    elif path in HTTP_DENIED_PATHS or path.startswith(HTTP_DENIED_PREFIXES):
        status = 'unsupported'
    elif method == 'GET' and path in HTTP_IMPLEMENTED:
        status = 'implemented'
    elif (file == 'config_env' and path == '/api/config' and method == 'PUT'
          or file == 'tools' and path == '/api/tools/toolsets/{name}' and method == 'PUT'
          or file == 'ops' and path.startswith('/api/memory')):
        status = 'stub'
    else:
        status = HTTP_DEFAULTS[file]
    result = decision(status, path, 'M1 restricted projection / deferred UI / excluded host surface.', evidence)
    if (method, path) in HTTP_DENIED_ROUTES and not external:
        result['reason'], result['detail'] = HTTP_DENIED_ROUTES[method, path]
    elif (method, path) == ('GET', '/api/sessions/{session_id}/export'):
        result.update(reason='server_download_deferred',
                      detail='서버 다운로드 방식 내보내기는 유예; 로컬 파일 저장은 허용하지 않음')
    if method != 'WEBSOCKET' and path.startswith('/api/mcp/'):
        result['denied_actions'] = ['stdio', 'SSE']
    if status == 'implemented':
        result['projection'] = 'principal_owned_server_allowlisted_no_secrets'
    if method == 'WEBSOCKET' and status != 'implemented':
        result['rejection'] = 'auth_first_then_upgrade_403_proposed'
    if (file in {'cron', 'mcp', 'memory_providers', 'skills', 'profiles'}
            or file == 'ops' and path.startswith('/api/memory')) and status == 'stub':
        result['reason'] = 'deferred_milestone_4' if file == 'cron' else 'deferred_milestone_3'
    return result
