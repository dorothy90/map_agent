"""Offline comparison CLI; never imports or modifies the embedded engine."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from .compare import excluded, json_bytes, make_report, render_markdown
from .patch_check import check_patches, git_environment


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, env=git_environment()).stdout


def _paths(root):
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not excluded((relative / d).as_posix())
                         and not (Path(directory) / d).is_symlink())
        for name in sorted(files):
            path = (relative / name).as_posix()
            if not excluded(path):
                yield path


def _read(root, paths):
    result = {}
    for path in sorted(paths):
        if excluded(path):
            continue
        target = root / path
        # Never follow a link into an environment file or outside the input root.
        if not target.resolve().is_relative_to(root.resolve()) or excluded(target.resolve().relative_to(root.resolve()).as_posix()):
            raise ValueError('Input contains an unsafe symbolic link')
        if target.is_symlink():
            result[path] = os.readlink(target).encode('utf-8')
        else:
            result[path] = target.read_bytes()
    return result


def read_hermes(root, expected_commit):
    if not root.is_dir():
        raise ValueError('Hermes root must be an existing directory')
    # A plain fixture nested inside another repo must not inherit that repo's HEAD.
    is_git = (root / '.git').exists() or (root / '.git').is_file()
    if is_git:
        commit = _git(root, 'rev-parse', 'HEAD').decode().strip()
        if not expected_commit or not commit.startswith(expected_commit):
            raise ValueError('Hermes commit does not match expected commit')
        paths = [p.decode('utf-8') for p in _git(root, 'ls-files', '-z').split(b'\0') if p]
        clean = not bool(_git(root, 'status', '--porcelain'))
    else:
        commit, clean, paths = None, None, list(_paths(root))
    return _read(root, paths), {'commit': commit, 'clean': clean,
                                'file_selection': 'git ls-files' if is_git else 'recursive directory',
                                'content_source': 'working tree bytes; symlinks hash link target text'}


def build_report(engine_root, hermes_root, expected_commit='5894e750'):
    engine_root, hermes_root = Path(engine_root), Path(hermes_root)
    if not engine_root.is_dir():
        raise ValueError('Engine root must be an existing directory')
    engine = _read(engine_root, _paths(engine_root))
    if 'UPSTREAM.json' not in engine or 'PATCHES.diff' not in engine:
        raise ValueError('Engine requires UPSTREAM.json and PATCHES.diff')
    upstream = json.loads(engine['UPSTREAM.json'])
    if (not isinstance(upstream, dict) or not isinstance(upstream.get('commit'), str)
            or any(not isinstance(upstream.get(k), dict) for k in ('files', 'local_files'))):
        raise ValueError('Invalid UPSTREAM metadata')
    hermes, source = read_hermes(hermes_root, expected_commit)
    patches = check_patches(engine['PATCHES.diff'], hermes, upstream['files'])
    from .sync import stored_scope
    return make_report(engine, hermes, upstream, source, patches, expected_commit,
                       scope=stored_scope(engine_root))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root', type=Path, default=Path('harness/engine'))
    parser.add_argument('--hermes-root', type=Path, default=Path(os.environ.get('HERMES_CONTRACT_ROOT', '/tmp/hermes-agent-ro')))
    parser.add_argument('--expected-commit', default='5894e750')
    parser.add_argument('--output-dir', type=Path, default=Path('harness/engine_sync'))
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('action', nargs='?', choices=['sync'],
                        help='sync defaults to dry-run; no action preserves comparison reporting')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    try:
        if not args.expected_commit or any(c not in '0123456789abcdefABCDEF' for c in args.expected_commit):
            raise ValueError('Expected commit must be a hexadecimal prefix')
        if args.action == 'sync' or args.apply or args.dry_run:
            from .sync import synchronize
            print(json.dumps(synchronize(args.engine_root, args.hermes_root, apply=args.apply),
                             ensure_ascii=False, sort_keys=True))
            return 0
        outputs = [args.output_dir / f'report_{args.expected_commit}.{suffix}' for suffix in ('json', 'md')]
        if not args.check_only:
            for path in [args.output_dir, *outputs]:
                if any(path.resolve().is_relative_to(root.resolve()) for root in (args.engine_root, args.hermes_root)):
                    raise ValueError('Output must not be inside engine or Hermes input')
        report = build_report(args.engine_root, args.hermes_root, args.expected_commit)
        if not args.check_only:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            outputs[0].write_bytes(json_bytes(report))
            outputs[1].write_text(render_markdown(report), encoding='utf-8')
        print(json.dumps({'summary': report['summary'], 'patches': [
            {'path': p['path'], 'applies': p['applies'], 'conflict_hunks': [h['header'] for h in p['hunks'] if not h['applies']]}
            for p in report['patches']], 'integrity_mismatched': report['integrity']['mismatched']},
            ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        # Do not echo source data, subprocess stderr or malformed JSON into logs.
        print('engine_sync: input/output error; check roots, metadata, patch format and expected commit', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
