"""Compare a verified reference tree with an internal checkout. Uses Python 3.11 stdlib."""
import argparse
import csv
import hashlib
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile


COLUMNS = ('action', 'state', 'path', 'destination', 'class', 'role')


def table(path):
    with path.open(encoding='utf-8-sig', newline='') as src:
        return list(csv.DictReader(src, delimiter='\t'))


def location(root, name):
    rel = PurePosixPath(name)
    if (not rel.parts or rel.is_absolute() or '..' in rel.parts or any(p.lower() == '.git' for p in rel.parts)
            or rel.as_posix() != name or '\\' in name or ':' in name):
        raise ValueError(f'Unsafe relative path: {name!r}')
    value = root
    for part in rel.parts:
        value = value / part
        if value.is_symlink():
            raise ValueError(f'Symlink at {value}')
        if value != root / rel and value.exists() and not value.is_dir():
            raise ValueError(f'Parent is a file: {value}')
    return value


def mode(row):
    return int(row['mode'], 8) & 0o777


def identical(path, row):
    return (path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == row['sha256']
            and (os.name == 'nt' or bool(path.stat().st_mode & stat.S_IXUSR) == bool(mode(row) & stat.S_IXUSR)))


def state(root, row, destination):
    try:
        p = location(root, destination)
        if not p.exists():
            return 'MISSING'
        return 'IDENTICAL' if identical(p, row) else 'DIFFERENT'
    except ValueError:
        return 'BLOCKED'


def write_new(src, dst, row):
    data = src.read_bytes()
    if hashlib.sha256(data).hexdigest() != row['sha256']:
        raise ValueError(f"Reference changed: {row['path']}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Install a complete file or leave the destination untouched.
    temp = tempfile.NamedTemporaryFile(dir=dst.parent, delete=False)
    try:
        with temp as out:
            out.write(data)
        os.chmod(temp.name, mode(row))
        os.link(temp.name, dst)
    finally:
        os.unlink(temp.name)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['plan', 'apply'])
    ap.add_argument('--manifest', type=Path, required=True)
    ap.add_argument('--target', type=Path, required=True)
    ap.add_argument('--sheet', type=Path, required=True)
    ap.add_argument('--source', type=Path)
    args = ap.parse_args()
    root = args.target.resolve(strict=True)
    if not (root / '.git').exists():
        ap.error('--target must be the root of an internal Git checkout')
    rows = table(args.manifest)
    by_name = {row['path']: row for row in rows}
    if not rows or len(by_name) != len(rows):
        ap.error('Empty manifest or duplicate source paths')
    if args.command == 'plan':
        with args.sheet.open('x', encoding='utf-8', newline='') as dest:
            writer = csv.DictWriter(dest, fieldnames=COLUMNS, delimiter='\t', lineterminator='\n')
            writer.writeheader()
            for row in rows:
                result = state(root, row, row['destination'])
                writer.writerow({**{k: row[k] for k in ('path', 'destination', 'class', 'role')},
                                 'action': 'HOLD', 'state': result})
        if os.name == 'nt':
            print('Windows compares file bytes. Check and stage executable bits in Git before deployment.')
        print(f'{len(rows)} paths compared. Review the TSV and set ADD on approved missing paths.')
        return
    if not args.source:
        ap.error('apply requires --source (verified extracted source tree)')
    source = args.source.resolve(strict=True)
    sheet = table(args.sheet)
    if len(sheet) != len(rows) or {r['path'] for r in sheet} != set(by_name):
        ap.error('The decision sheet must contain every source path exactly once')
    pending = []
    occupied = set()
    for decision in sheet:
        row = by_name[decision['path']]
        if decision['action'] not in ('HOLD', 'KEEP', 'MERGE', 'ADD'):
            ap.error(f"Unknown action: {decision['action']}")
        if decision['action'] != 'ADD':
            continue
        src = location(source, row['path'])
        dst = location(root, decision['destination'])
        if dst in occupied or not identical(src, row):
            ap.error(f'Duplicate destination or invalid reference: {src}')
        occupied.add(dst)
        if dst.exists():
            if not identical(dst, row):
                ap.error(f'Existing destination needs a manual merge: {dst}')
        else:
            pending.append((src, dst, row))
    for src, dst, row in pending:
        write_new(src, dst, row)
    print(f'Added {len(pending)} new files. Review and stage in the internal branch.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError) as exc:
        sys.exit(str(exc))
