"""Install reviewed Yield skills to a single stopped staging profile. Preview by default."""
import argparse
from hashlib import sha256
from pathlib import Path
import os
import shutil
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project-root', type=Path, required=True, help='Checkout containing 08-YieldAgent')
    parser.add_argument('--principal', required=True, help='Exact authenticated principal ID')
    parser.add_argument('--with-demo-memory', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.principal.strip():
        parser.error('principal must not be blank')
    root = args.project_root.resolve(strict=True)
    if not (root / '08-YieldAgent/harness/native_hermes.py').is_file():
        parser.error('expected the deployed checkout root')
    seed = Path(__file__).resolve().with_name('profile-seed')
    profile = root / '.runtime/hermes/profiles' / sha256(args.principal.encode()).hexdigest()
    files = [(p, profile / p.relative_to(seed)) for p in sorted((seed/'skills').glob('*/SKILL.md'))]
    if len(files) != 4:
        parser.error('expected four skill definitions; verify transfer integrity')
    if args.with_demo_memory:
        files.append((seed/'optional-demo/MEMORY.md', profile/'memories/MEMORY.md'))
    print('profile:', profile)
    missing = []
    for src, dest in files:
        if dest.resolve() != dest:
            parser.error(f'symlink in target path: {dest}')
        if dest.exists():
            if not dest.is_file() or dest.read_bytes() != src.read_bytes():
                parser.error(f'target differs: {dest}; compare inside the company before merging')
            print('identical:', dest.relative_to(profile))
        else:
            missing.append((src, dest))
            print('to add:', dest.relative_to(profile))
    if not args.apply:
        print('preview only')
        return
    os.umask(0o077)
    for src, dest in missing:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Publish only a complete file. Refuse races with a newly created destination.
        with tempfile.NamedTemporaryFile(dir=dest.parent) as temp:
            with src.open('rb') as source:
                shutil.copyfileobj(source, temp)
            temp.flush()
            os.link(temp.name, dest)
    print('installed:', len(missing))


if __name__ == '__main__':
    main()
