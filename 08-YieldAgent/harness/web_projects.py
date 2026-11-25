"""Pathless Hermes projects for employee-owned web chats."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import stat


def install_web_projects(registry, profile, database, session_project):
    from hermes_cli import projects_db
    from tools import project_tools

    store = profile / 'web-projects' / hashlib.sha256(database.encode()).hexdigest() / 'projects.db'
    name = 'desktop_project'
    schema = deepcopy(project_tools.registry.get_schema(name))
    schema['description'] = ('Manage named projects in the chat sidebar. create groups the current chat in a named '
                             'project, switch moves this chat by project name, slug or ID, list shows your projects '
                             'and which one this chat belongs to.')
    schema['parameters']['properties'].pop('path', None)
    schema['parameters']['additionalProperties'] = False

    def handle(args, **_):
        action = args.get('action')
        token = args.get('name', '')
        if (set(args) - {'action', 'name'} or action not in ('create', 'switch', 'list')
                or not isinstance(token, str) or (action != 'list' and not token.strip())):
            return json.dumps({'success': False, 'error': 'Use create or switch with a project name, or list.'})
        if store.resolve() != store:
            raise ValueError('Invalid project store')
        store.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        for entry in store.parent.iterdir():
            try:
                info = entry.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Invalid project store')
        with projects_db.connect_closing(store) as conn:
            active = session_project(None)
            if action == 'list':
                result = {'active_id': active, 'projects': [
                    {'id': p.id, 'slug': p.slug, 'name': p.name, 'active': p.id == active}
                    for p in projects_db.list_projects(conn)]}
            else:
                if action == 'create':
                    # A retried create, including from concurrent workers, must re-use its named group.
                    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS web_project_name ON projects(name COLLATE NOCASE)')
                    try:
                        pid = projects_db.create_project(conn, name=token)
                    except sqlite3.IntegrityError:
                        row = conn.execute('SELECT id FROM projects WHERE name=? COLLATE NOCASE', (token.strip(),)).fetchone()
                        if row is None:
                            raise
                        pid = row['id']
                    project = projects_db.get_project(conn, pid)
                else:
                    project = project_tools._resolve(conn, token)
                if not project or project.archived:
                    return json.dumps({'success': False, 'error': 'Project not found'})
                session_project({'id': project.id, 'name': project.name})
                result = {'success': True, 'id': project.id, 'slug': project.slug, 'name': project.name}
        return json.dumps(result, ensure_ascii=False)

    registry.register(name=name, toolset='web_projects', schema=schema, handler=handle, override=True)


async def current_project(sessions, run, project):
    scope = {'principal_id': run['principal_id'], 'id': run['session_id'], 'deleted': {'$ne': True}}
    if project is None:
        row = await sessions.find_one(scope, {'web_project.id': 1})
        if row is not None:
            return (row.get('web_project') or {}).get('id')
    elif (await sessions.update_one(scope, {'$set': {'web_project': project}})).matched_count:
        return project['id']
    raise PermissionError('Originating chat is unavailable')
