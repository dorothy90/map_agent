"""Offline, explicit source synchronization. Default execution is read-only."""
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess

from .compare import classify, comparable, excluded, in_scope, scope_rules
from .patch_check import git_environment

UPSTREAM_COMMIT = '5894e750c3dba980d43b25aa29e3958149ba2c61'


def _git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, env=git_environment()).stdout


def tracked_paths(root):
    if _git(root, 'rev-parse', 'HEAD').decode().strip() != UPSTREAM_COMMIT:
        raise ValueError('Hermes commit does not match required commit')
    if _git(root, 'status', '--porcelain', '--untracked-files=no'):
        raise ValueError('Hermes tracked files must match the commit')
    return [p.decode('utf-8') for p in _git(root, 'ls-files', '-z').split(b'\0') if p]


def safe_path(root, path):
    relative = PurePosixPath(path)
    if not path or relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe path')
    target = root / path
    if not target.resolve().is_relative_to(root):
        raise ValueError('Unsafe path outside root')
    for part in (target, *target.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('Unsafe symbolic link path')
    if target.is_file() and target.stat().st_nlink != 1:
        raise ValueError('Unsafe hard link path')
    return target


def engine_paths(root):
    paths = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        relative = Path(directory).relative_to(root)
        dirs[:] = sorted(d for d in dirs if not excluded((relative / d).as_posix()))
        for name in dirs + files:
            path = (relative / name).as_posix()
            if not comparable(path):
                continue
            safe_path(root, path)
            if name in files:
                paths.append(path)
    return sorted(paths)


def stored_scope(engine_root):
    """Reuse the PR-4a scope, so new nested extensions cannot broaden later syncs."""
    path = Path(engine_root).resolve().parent / 'engine_sync' / 'scope_5894e750.json'
    return json.loads(path.read_bytes()) if path.is_file() else None


def synchronize(engine_root, hermes_root, *, apply=False):
    engine_root, hermes_root = Path(engine_root).resolve(), Path(hermes_root).resolve()
    if (not engine_root.is_dir() or not hermes_root.is_dir()
            or engine_root.is_relative_to(hermes_root) or hermes_root.is_relative_to(engine_root)):
        raise ValueError('Input roots must be separate existing directories')
    tracked = tracked_paths(hermes_root)
    # Validate all names before any reading or mutation; excluded files are never opened.
    for path in tracked:
        if not path or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts:
            raise ValueError('Unsafe source path')
    current = engine_paths(engine_root)
    scope = stored_scope(engine_root) or scope_rules(current)
    omitted = sorted(p for p in tracked if excluded(p))
    selected = sorted(p for p in tracked if comparable(p)
                      and (in_scope(p, scope) or p in current))
    source = {}
    for path in selected:
        target = safe_path(hermes_root, path)
        safe_path(engine_root, path)
        if not target.is_file():
            raise ValueError('Source path must be a regular file')
        source[path] = target.read_bytes()
    engine = {p: safe_path(engine_root, p).read_bytes() for p in current}
    comparison = classify(engine, source, {'files': {}, 'local_files': {}}, scope=scope)
    deleted = [p for p in comparison['deleted'] if in_scope(p, scope)]
    changed = [p['path'] for p in comparison['changed']]
    mode_changed = [p for p in selected if p in engine and
                    stat.S_IMODE((engine_root / p).stat().st_mode) !=
                    stat.S_IMODE((hermes_root / p).stat().st_mode)]
    plan = {'commit': UPSTREAM_COMMIT, 'apply': apply, 'scope': scope,
            'counts': {'added': len(comparison['added']['in_scope']),
                       'changed': len(changed), 'deleted': len(deleted)},
            'added': comparison['added']['in_scope'], 'changed': changed,
            'deleted': deleted, 'mode_changed': mode_changed,
            'excluded': omitted, 'selected_count': len(selected)}
    if apply:
        for path in selected:
            target = safe_path(engine_root, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_path(hermes_root, path), target)
            shutil.copymode(hermes_root / path, target)
        for path in deleted:
            target = safe_path(engine_root, path)
            target.unlink()
            parent = target.parent
            while parent != engine_root and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    return plan
