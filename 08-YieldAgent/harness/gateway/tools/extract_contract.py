"""Offline extractor. Run from 08-YieldAgent:

python3 -B -m harness.gateway.tools.extract_contract --hermes-root /tmp/hermes-agent-ro

HERMES_CONTRACT_ROOT is the alternative input. No upstream import, server, network,
or upstream code execution (only read-only git rev-parse). The only write is --output (default: local manifest.json).
Static AST handles route decorators and authority publication sites. Dynamic plugin
mounts and arbitrary runtime-generated event names are explicitly outside this scan.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from ..policy import UPSTREAM, UPSTREAM_COMMIT, event_policy, request_policy, route_policy, rpc_policy

TS_PATH = 'apps/shared/src/gateway-contract.generated.ts'


def interface(text, name):
    match = re.search(r'^export interface ' + name + r' \{\n(.*?)^\}', text, re.M | re.S)
    if not match:
        raise ValueError(f'Missing generated interface {name}')
    rows = {}
    for row in re.finditer(r"^  (?:'([^']+)'|(\w+)): ([^\n]+)", match[1], re.M):
        key = row[1] or row[2]
        if key in rows:
            raise ValueError(f'Duplicate {name}.{key}')
        line = text[:match.start(1) + row.start()].count('\n') + 1
        signature = row[3]
        types = re.fullmatch(r'\{ params: (.+?); result: (.+?) \}', signature)
        rows[key] = {'source': f'{TS_PATH}:{line}', 'schema': signature}
        if types:
            rows[key].update(params=types[1], result=types[2])
    if not rows:
        raise ValueError(f'Empty interface {name}')
    return rows


def authority_events(root):
    """Literal event_type / _publish_execution plus kind + '.settled' and gap frame.

    PendingControls kinds are extracted from its actual prompt dictionaries, not a
    handwritten event list. Internal progress queue names are intentionally excluded.
    """
    found = {}
    pending = root / 'gateway/session_pending_controls.py'
    pending_tree = ast.parse(pending.read_text())
    kinds = {v.value for n in ast.walk(pending_tree) if isinstance(n, ast.Dict)
             for k, v in zip(n.keys, n.values)
             if isinstance(k, ast.Constant) and k.value == 'kind'
             and isinstance(v, ast.Constant) and isinstance(v.value, str)}
    for path in sorted((root / 'gateway').glob('*.py')):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            values = []
            if isinstance(node, ast.Call):
                values = [k.value for k in node.keywords if k.arg == 'event_type']
                if isinstance(node.func, ast.Attribute) and node.func.attr == '_publish_execution':
                    values += node.args[:1]
            if path.name == 'session_events.py' and isinstance(node, ast.Dict):
                values += [v for k, v in zip(node.keys, node.values)
                           if isinstance(k, ast.Constant) and k.value == 'type']
            for value in values:
                names = []
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    names = [value.value]
                elif (path == pending and isinstance(value, ast.BinOp)
                      and isinstance(value.op, ast.Add) and isinstance(value.right, ast.Constant)
                      and value.right.value == '.settled'):
                    names = [kind + value.right.value for kind in sorted(kinds)]
                for name in names:
                    found.setdefault(name, []).append(f'{path.relative_to(root)}:{node.lineno}')
    return {name: sorted(set(sources)) for name, sources in found.items()}


def routes(root, paths, external=False):
    rows = []
    verbs = {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'websocket', 'api_route', 'route'}
    for path in sorted(paths):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not isinstance(dec.func, ast.Attribute) or dec.func.attr not in verbs:
                    continue
                if not dec.args or not isinstance(dec.args[0], ast.Constant):
                    raise ValueError(f'Nonliteral route at {path}:{dec.lineno}')
                route = dec.args[0].value
                methods = [dec.func.attr.upper()]
                if dec.func.attr in {'route', 'api_route'}:
                    methods = next((ast.literal_eval(k.value) for k in dec.keywords if k.arg == 'methods'), ['GET'])
                for method in methods:
                    method = method.upper()
                    rows.append({'name': f'{method} {route}', 'path': route, 'method': method,
                                 'source': f'{path.relative_to(root)}:{dec.lineno}',
                                 **route_policy(route, method, path.stem, external)})
    names = [row['name'] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate route identity; preserve distinct registration before proceeding')
    return sorted(rows, key=lambda row: row['name'])


def error_code_collisions(root):
    """Text grep plus Python integer AST catches -32_004 / -0x7d04 spellings.

    Static check only; dynamically computed/imported extension codes need PR-2 review.
    """
    hits = set()
    for folder in ('tui_gateway', 'gateway'):
        for path in sorted((root / folder).rglob('*')):
            if not path.is_file() or path.suffix not in {'.py', '.ts', '.js', '.json', '.md', '.yaml', '.yml'}:
                continue
            text = path.read_text()
            for line, content in enumerate(text.splitlines(), 1):
                if '-32004' in content:
                    hits.add(f'{path.relative_to(root)}:{line}')
            if path.suffix == '.py':
                for node in ast.walk(ast.parse(text)):
                    if (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub)
                            and isinstance(node.operand, ast.Constant) and node.operand.value == 32004):
                        hits.add(f'{path.relative_to(root)}:{node.lineno}')
    return sorted(hits)


def extract_contract(root):
    root = Path(root)
    revision = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              check=True, capture_output=True, text=True).stdout.strip()
    if revision != UPSTREAM_COMMIT:
        raise ValueError(f'Expected upstream {UPSTREAM_COMMIT}, got {revision}')
    text = (root / TS_PATH).read_text()
    inventories = {'legacy': interface(text, 'RpcMethods'),
                   'authority': interface(text, 'CanonicalRpcMethods')}
    rpc = []
    for name in sorted(set().union(*inventories.values())):
        modes = {mode: {**entries[name], **rpc_policy(name, mode)}
                 for mode, entries in inventories.items() if name in entries}
        classifications = {entry['classification'] for entry in modes.values()}
        if len(classifications) != 1:
            raise ValueError(f'Mode classifications diverge for {name}; explicit aggregate policy needed')
        first = next(iter(modes.values()))
        rpc.append({'name': name, **{k: first[k] for k in ('classification', 'reason', 'feature', 'detail', 'policy_source')},
                    'source': [entry['source'] for entry in modes.values()], 'modes': modes})
    generated = interface(text, 'BackendGatewayEventMap')
    emitted = authority_events(root)
    events = []
    for name in sorted(set(generated) | set(emitted)):
        sources = ([generated[name]['source']] if name in generated else []) + emitted.get(name, [])
        events.append({'name': name, 'source': sources,
                       'origins': (['generated'] if name in generated else []) + (['authority'] if name in emitted else []),
                       'schema': generated[name]['schema'] if name in generated else 'authority_source_payload',
                       **event_policy(name)})
    requests = [{'name': name, **schema, **request_policy(name)}
                for name, schema in sorted(interface(text, 'ServerRequestMap').items())]
    route_rows = routes(root, [*(root / 'hermes_cli/web_routers').glob('*.py'), root / 'hermes_cli/web_server.py'])
    external = routes(root, [root / 'hermes_cli/memory_oauth.py', root / 'hermes_cli/dashboard_auth/routes.py'], True)
    sections = {'rpc': rpc, 'events': events, 'server_requests': requests,
                'routes': route_rows, 'external_routes': external}
    files = {TS_PATH}
    for entries in sections.values():
        for entry in entries:
            sources = entry['source'] if isinstance(entry['source'], list) else [entry['source']]
            files.update(source.rsplit(':', 1)[0] for source in sources)
    return {'schema_version': 1, 'upstream': UPSTREAM, 'upstream_commit': revision,
            'classification_scope': 'milestone_1_target', 'runtime_connected': False,
            'source_hashes': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(files)},
            'boundaries': {'dynamic_plugin_routes': 'not_enumerated_disabled',
                           'spa_mount': 'deployment_only_not_an_api_route',
                           'auth_adapter': 'PR-2_not_connected',
                           'dynamic_error_codes': 'not_proven_by_static_scan'},
            **sections}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hermes-root', type=Path, default=os.environ.get('HERMES_CONTRACT_ROOT', '/tmp/hermes-agent-ro'))
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'manifest.json')
    args = parser.parse_args()
    # Never allow an output destination inside the upstream read-only checkout.
    if args.output.resolve().is_relative_to(args.hermes_root.resolve()):
        parser.error('--output must not be inside the upstream checkout')
    manifest = extract_contract(args.hermes_root)
    collisions = error_code_collisions(args.hermes_root)
    if collisions:
        parser.error('-32004 conflicts: ' + ', '.join(collisions))
    args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: len(manifest[key]) for key in ('rpc', 'events', 'server_requests', 'routes', 'external_routes')}))


if __name__ == '__main__':
    main()
