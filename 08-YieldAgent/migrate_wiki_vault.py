from __future__ import annotations

import argparse
import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationReport:
    planned: int
    copied: int
    identical: int


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def plan_migration(source: Path) -> list[Path]:
    return sorted(
        path
        for path in source.rglob("*")
        if path.is_file() and not path.name.endswith(".tmp")
    )


def migrate_vault(source: Path, target: Path, *, apply: bool) -> MigrationReport:
    source = source.resolve()
    target = target.resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"source Vault not found: {source}")
    files = plan_migration(source)
    if not apply:
        return MigrationReport(planned=len(files), copied=0, identical=0)

    copied = 0
    identical = 0
    for source_file in files:
        relative = source_file.relative_to(source)
        target_file = target / relative
        if target_file.exists():
            if sha256_file(source_file) != sha256_file(target_file):
                raise FileExistsError(f"different target file: {target_file}")
            identical += 1
            continue
        target_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_file)
        if sha256_file(source_file) != sha256_file(target_file):
            raise OSError(f"checksum mismatch: {target_file}")
        copied += 1
    return MigrationReport(planned=len(files), copied=copied, identical=identical)


def main() -> int:
    parser = argparse.ArgumentParser(description="copy and verify a Wiki Vault")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        report = migrate_vault(args.source, args.target, apply=args.apply)
    except (FileNotFoundError, FileExistsError, OSError) as exc:
        print(f"migration failed: {exc}")
        return 1
    print(f"source={args.source.resolve()}")
    print(f"target={args.target.resolve()}")
    print(
        f"planned={report.planned} copied={report.copied} "
        f"identical={report.identical}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
