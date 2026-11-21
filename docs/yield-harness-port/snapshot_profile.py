"""Snapshot approved, durable profile payload into a NEW private directory for inward transfer.

Stop all writers before running, including the gateway, background tasks and CLI.
Use for a single principal with its owner's authorization. Treat output as sensitive.
"""
import argparse
from contextlib import closing
from pathlib import Path
import os
import shutil
import sqlite3
import stat


def copy_file(source, dest):
    if not stat.S_ISREG(source.lstat().st_mode):
        raise ValueError(f'unexpected file type: {source}')
    dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with source.open('rb') as reader, dest.open('xb') as writer:
        shutil.copyfileobj(reader, writer)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--dest', type=Path, required=True)
    parser.add_argument('--skills', action='store_true')
    parser.add_argument('--memory', action='store_true')
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    if not (args.skills or args.memory or args.history):
        parser.error('select at least one data class')
    source, dest = args.source.resolve(strict=True), args.dest.resolve()
    if not source.is_dir() or dest == source or source in dest.parents or dest in source.parents:
        parser.error('choose separate source and new destination directories')
    os.umask(0o077)
    dest.mkdir(mode=0o700, exist_ok=False)
    # Transfer only named skills (including their own reference/script/template files).
    if args.skills:
        skills = source/'skills'
        if skills.is_symlink() or not skills.is_dir():
            raise ValueError('expected a local skills directory')
        for directory, dirs, names in os.walk(skills):
            here = Path(directory)
            dirs[:] = [name for name in dirs if not name.startswith('.') and name != '__pycache__']
            if any((here/name).is_symlink() for name in dirs):
                raise ValueError('symlink in skills; review and resolve its content separately')
            if 'SKILL.md' in names:
                for file in here.rglob('*'):
                    if file.is_symlink():
                        raise ValueError(f'symlink in skill: {file}')
                    if any(p.startswith('.') or p == '__pycache__' for p in file.relative_to(here).parts):
                        continue
                    if not file.is_dir():
                        copy_file(file, dest/file.relative_to(source))
                dirs.clear()
    if args.memory:
        if (source/'memories').is_symlink():
            raise ValueError('symlink in memories')
        for name in ('MEMORY.md', 'USER.md'):
            path = source/'memories'/name
            if path.exists():
                copy_file(path, dest/'memories'/name)
    if args.history:
        path = source/'state.db'
        if not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('state.db must be a regular file')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as reader:
            with closing(sqlite3.connect(dest/'state.db')) as writer:
                reader.backup(writer)
                if writer.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('SQLite integrity check failed')
        sessions = source/'sessions'
        if sessions.is_symlink():
            raise ValueError('symlink in sessions')
        if sessions.exists():
            for file in sessions.rglob('*'):
                if file.is_symlink():
                    raise ValueError(f'symlink in sessions: {file}')
                if file.is_file():
                    copy_file(file, dest/file.relative_to(source))
    # Missing marker means this directory must not be restored after an interrupted snapshot.
    (dest/'SNAPSHOT_COMPLETE').write_text('Complete. Review payload before inward transfer.\n')
    print('snapshot complete; files:', sum(p.is_file() for p in dest.rglob('*'))-1)


if __name__ == '__main__':
    main()
