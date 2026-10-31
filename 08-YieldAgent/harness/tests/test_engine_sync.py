"""Offline tests; mutable fixtures stay under tmp_path."""
import hashlib
import json
import shutil
import subprocess

import pytest

from harness.engine_sync.compare import classify, digest, json_bytes, render_markdown, line_counts
from harness.engine_sync.__main__ import build_report, main
from harness.engine_sync.patch_check import check_patches


def put(root, name, data):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def snapshot(root):
    return {str(p.relative_to(root)): digest(p.read_bytes())
            for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def inputs(tmp_path):
    engine, hermes = tmp_path / 'engine', tmp_path / 'hermes'
    for root in (engine, hermes):
        put(root, 'same.py', b'same\n')
        put(root, 'pkg/edit.py', b'old\n')
    put(engine, 'pkg/edit.py', b'local\n')
    put(engine, 'gone.py', b'gone\n')
    put(hermes, 'pkg/new.txt', b'new\n')
    put(hermes, 'new.py', b'new\n')
    put(hermes, 'docs/new.py', b'outside\n')
    put(hermes, 'new.txt', b'outside\n')
    put(engine, '__pycache__/x.pyc', b'cache')
    pin = {'commit': 'old', 'repository': 'fixture', 'adaptations': [],
           'files': {'same.py': digest(b'same\n'), 'pkg/edit.py': digest(b'old\n'),
                     'gone.py': digest(b'wrong\n')},
           'local_files': {'pkg/edit.py': digest(b'local\n')}}
    put(engine, 'UPSTREAM.json', json.dumps(pin).encode())
    put(engine, 'PATCHES.diff', b'')
    return engine, hermes


def test_hash_and_lines():
    assert digest(b'abc') == hashlib.sha256(b'abc').hexdigest()
    assert line_counts(b'a\nb\n', b'a\nc\nd\n') == {'added': 2, 'removed': 1}
    assert line_counts(b'\x00', b'a') is None
    assert line_counts(b'a' * (1024 * 1024 + 1), b'a') is None


def test_classification_scope_and_exclusions():
    e = {'a.py': b'1', 'pkg/a.txt': b'a', 'gone': b'g', 'UPSTREAM.json': b'{}',
         'PATCHES.diff': b'', '__pycache__/a.pyc': b'c', 'pkg/a.pyc': b'c'}
    h = {'a.py': b'1', 'pkg/a.txt': b'b', 'pkg/new.bin': b'c', 'new.py': b'x',
         'docs/new.py': b'x', 'new.rst': b'x', '__pycache__/z.pyc': b'c'}
    result = classify(e, h, {'files': {}, 'local_files': {}})
    assert result['identical'] == ['a.py']
    assert result['deleted'] == ['gone']
    assert [x['path'] for x in result['changed']] == ['pkg/a.txt']
    assert result['added']['in_scope'] == ['new.py', 'pkg/new.bin']
    assert result['added']['out_of_scope'] == {'count': 2, 'by_top_level': {'.': 1, 'docs': 1}}


def test_end_to_end_deterministic_and_read_only(inputs):
    engine, hermes = inputs
    before = snapshot(engine), snapshot(hermes)
    report = build_report(engine, hermes, '5894e750')
    assert json_bytes(report) == json_bytes(build_report(engine, hermes, '5894e750'))
    assert report['hermes']['commit'] is None
    assert report['summary'] == {'identical': 1, 'changed': 0, 'patched_local': 1, 'added_in_scope': 2,
                                  'added_out_of_scope': 2, 'deleted': 1}
    assert report['comparison']['changed'] == []
    assert report['comparison']['patched_local'] == ['pkg/edit.py']
    assert report['integrity']['matched'] == 2
    assert report['integrity']['mismatched'] == 1
    assert report['integrity']['mismatch_paths'] == ['gone.py']
    assert len(report['engine_hashes']) == 3
    md = render_markdown(report)
    for heading in ('요약', '패치 호환성', '로컬 패치', '변경 파일', '무결성', '재현', '한계'):
        assert heading in md
    assert (snapshot(engine), snapshot(hermes)) == before


PATCH = b'''--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 one
-old
+new
 three
@@ -7,3 +7,3 @@
 seven
-before
+after
 nine
'''


@pytest.mark.skipif(not shutil.which('git'), reason='git required')
@pytest.mark.parametrize('content, expected', [
    (b'one\nold\nthree\nfour\nfive\nsix\nseven\nbefore\nnine\n', [True, True]),
    (b'one\nold\nthree\nfour\nfive\nsix\nseven\nDIFFERENT\nnine\n', [True, False]),
    (None, [False, False]),
])
def test_patch_file_and_individual_hunks(tmp_path, content, expected):
    root = tmp_path / 'source'
    root.mkdir()
    put(root, 'PATCHES.diff', PATCH)
    if content is not None:
        put(root, 'file.py', content)
    before = snapshot(root)
    result = check_patches(PATCH, {} if content is None else {'file.py': content}, {})[0]
    assert result['applies'] == all(expected)
    assert [h['applies'] for h in result['hunks']] == expected
    assert result['missing_in_hermes'] == (content is None)
    assert all(h['header'].startswith('@@') for h in result['hunks'])
    assert all(h['error'] for h in result['hunks'] if not h['applies'])
    assert snapshot(root) == before


def test_cli_check_only_and_output_guard(inputs, tmp_path):
    engine, hermes = inputs
    before = snapshot(engine), snapshot(hermes)
    args = ['--engine-root', str(engine), '--hermes-root', str(hermes)]
    output = tmp_path / 'output'
    assert main(args + ['--check-only', '--output-dir', str(output)]) == 0
    assert not output.exists()
    assert main(args + ['--output-dir', str(output)]) == 0
    assert json.loads((output / 'report_5894e750.json').read_bytes())['summary']['patched_local'] == 1
    for destination in (engine, hermes):
        assert main(args + ['--output-dir', str(destination)]) != 0
    assert (snapshot(engine), snapshot(hermes)) == before


@pytest.mark.skipif(not shutil.which('git'), reason='git required')
def test_git_commit_guard_and_dirty_tracking(inputs, monkeypatch):
    engine, hermes = inputs
    # Simulate read-only Git responses: this suite must never write a Git index.
    from harness.engine_sync import __main__ as cli
    (hermes / '.git').mkdir()
    revision = '5894e750' + '0' * 32
    tracked = [p.relative_to(hermes).as_posix() for p in hermes.rglob('*') if p.is_file()]
    def git_read(root, *args):
        if args == ('rev-parse', 'HEAD'):
            return revision.encode()
        if args == ('ls-files', '-z'):
            return ('\0'.join(tracked) + '\0').encode()
        if args == ('status', '--porcelain'):
            return b'?? untracked.py' if (hermes / 'untracked.py').exists() else b''
        raise AssertionError(args)
    monkeypatch.setattr(cli, '_git', git_read)
    with pytest.raises(ValueError, match='commit'):
        build_report(engine, hermes, 'incorrect')
    assert build_report(engine, hermes, revision[:8])['hermes']['clean'] is True
    put(hermes, 'untracked.py', b'ignored')
    report = build_report(engine, hermes, revision[:8])
    assert report['hermes']['clean'] is False
    assert 'untracked.py' not in report['comparison']['added']['in_scope']


def test_nested_extension_and_folder_boundary():
    result = classify({'pkg/a.txt': b'a'}, {'new.txt': b'b', 'pkg2/a.txt': b'b'},
                      {'files': {}, 'local_files': {}})
    assert result['added']['in_scope'] == ['new.txt']
    assert result['added']['out_of_scope']['by_top_level'] == {'pkg2': 1}


def test_unlisted_missing_and_limited_integrity():
    from harness.engine_sync.compare import integrity
    result = integrity({'a': 'wrong', 'b': 'wrong', 'extra': 'new'},
                       {'files': {'a': 'old', 'b': 'old', 'missing': 'old'}, 'local_files': {}}, limit=1)
    assert (result['mismatched'], result['unlisted'], result['missing']) == (2, 1, 1)
    assert result['mismatch_paths'] == ['a']
    assert result['mismatch_paths_omitted'] == 1


def test_env_excluded_without_opening(inputs, monkeypatch):
    from pathlib import Path
    engine, hermes = inputs
    # Empty fixtures only; never read actual environment files.
    for root in (engine, hermes):
        put(root, '.env', b'')
        put(root, '.env.example', b'')
    original = Path.read_bytes
    def guarded(path):
        assert not path.name.startswith('.env')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guarded)
    report = build_report(engine, hermes)
    assert not any(p.startswith('.env') for p in report['engine_hashes'])


def test_output_symlink_into_input_rejected(inputs, tmp_path):
    engine, hermes = inputs
    output = tmp_path / 'output'
    output.mkdir()
    (output / 'report_5894e750.json').symlink_to(engine / 'same.py')
    before = snapshot(engine)
    assert main(['--engine-root', str(engine), '--hermes-root', str(hermes),
                 '--output-dir', str(output)]) == 2
    assert snapshot(engine) == before


def test_bad_patch_path_rejected():
    with pytest.raises(ValueError, match='path'):
        check_patches(PATCH.replace(b'file.py', b'../file.py'), {}, {})


@pytest.fixture
def sync_inputs(inputs, monkeypatch):
    from harness.engine_sync import sync
    engine, hermes = inputs
    tracked = [p.relative_to(hermes).as_posix() for p in hermes.rglob('*') if p.is_file()]
    monkeypatch.setattr(sync, 'tracked_paths', lambda root: tracked)
    return engine, hermes, tracked


def test_sync_copy_overwrite_delete_modes_and_dry_run(sync_inputs):
    from harness.engine_sync.sync import synchronize
    engine, hermes, tracked = sync_inputs
    (hermes / 'new.py').chmod(0o755)
    put(engine, 'pkg/empty/gone.py', b'gone')
    before = snapshot(engine), snapshot(hermes)
    plan = synchronize(engine, hermes)
    assert plan['counts'] == {'added': 2, 'changed': 1, 'deleted': 2}
    assert (snapshot(engine), snapshot(hermes)) == before
    synchronize(engine, hermes, apply=True)
    assert (engine / 'pkg/edit.py').read_bytes() == b'old\n'
    assert (engine / 'new.py').read_bytes() == b'new\n'
    assert (engine / 'new.py').stat().st_mode & 0o777 == 0o755
    assert not (engine / 'gone.py').exists()
    assert not (engine / 'pkg/empty').exists()
    assert not (engine / 'docs').exists()
    assert not (engine / 'new.txt').exists()
    assert (engine / 'UPSTREAM.json').exists()
    assert (engine / '__pycache__/x.pyc').exists()
    assert snapshot(hermes) == before[1]


def test_sync_env_paths_never_read(sync_inputs, monkeypatch):
    from pathlib import Path
    from harness.engine_sync.sync import synchronize
    engine, hermes, tracked = sync_inputs
    tracked.extend(['pkg/.env', 'pkg/.env.sample'])
    original = Path.read_bytes
    def guard(path):
        assert not path.name.startswith('.env')
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', guard)
    plan = synchronize(engine, hermes, apply=True)
    assert plan['excluded'] == ['pkg/.env', 'pkg/.env.sample']
    assert not (engine / 'pkg/.env').exists()


@pytest.mark.parametrize('path', ['../escape.py', '/tmp/escape.py', 'pkg/../../escape.py'])
def test_sync_path_escape_rejected(sync_inputs, path):
    from harness.engine_sync.sync import synchronize
    engine, hermes, tracked = sync_inputs
    tracked.append(path)
    before = snapshot(engine)
    with pytest.raises(ValueError, match='path'):
        synchronize(engine, hermes, apply=True)
    assert snapshot(engine) == before


@pytest.mark.parametrize('side', ['engine', 'hermes'])
def test_sync_symlink_escape_rejected(sync_inputs, tmp_path, side):
    from harness.engine_sync.sync import synchronize
    engine, hermes, tracked = sync_inputs
    outside = tmp_path / 'outside.py'
    outside.write_bytes(b'outside')
    root = engine if side == 'engine' else hermes
    (root / 'same.py').unlink()
    (root / 'same.py').symlink_to(outside)
    with pytest.raises(ValueError, match='path'):
        synchronize(engine, hermes, apply=True)
    assert outside.read_bytes() == b'outside'


def test_sync_commit_guard(tmp_path, monkeypatch):
    from harness.engine_sync import sync
    monkeypatch.setattr(sync, '_git', lambda root, *args: b'wrong')
    with pytest.raises(ValueError, match='commit'):
        sync.tracked_paths(tmp_path)


def test_patched_local_requires_original_and_local_hashes():
    from harness.engine_sync.compare import classify
    upstream = {'files': {'a.py': digest(b'original')}, 'local_files': {'a.py': digest(b'patched')}}
    result = classify({'a.py': b'patched'}, {'a.py': b'original'}, upstream)
    assert result['changed'] == []
    assert result['patched_local'] == ['a.py']
    assert len(classify({'a.py': b'other'}, {'a.py': b'original'}, upstream)['changed']) == 1
    assert len(classify({'a.py': b'patched'}, {'a.py': b'new upstream'}, upstream)['changed']) == 1


def test_fixed_scope_does_not_expand_with_new_nested_extensions(sync_inputs):
    from harness.engine_sync.sync import synchronize
    engine, hermes, tracked = sync_inputs
    scope_dir = engine.parent / 'engine_sync'
    scope_dir.mkdir()
    (scope_dir / 'scope_5894e750.json').write_text(json.dumps({
        'top_level_directories': ['pkg'], 'top_level_extensions': ['.py']}))
    put(hermes, 'pkg/new.js', b'js')
    put(hermes, 'top.js', b'outside')
    tracked.extend(['pkg/new.js', 'top.js'])
    synchronize(engine, hermes, apply=True)
    plan = synchronize(engine, hermes)
    assert plan['counts'] == {'added': 0, 'changed': 0, 'deleted': 0}
    assert (engine / 'pkg/new.js').exists()
    assert not (engine / 'top.js').exists()


def test_sync_cli_defaults_to_dry_run(sync_inputs, tmp_path, capsys):
    engine, hermes, tracked = sync_inputs
    before = snapshot(engine), snapshot(hermes)
    args = ['sync', '--engine-root', str(engine), '--hermes-root', str(hermes),
            '--output-dir', str(tmp_path / 'output')]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)['apply'] is False
    assert (snapshot(engine), snapshot(hermes)) == before
    assert not (tmp_path / 'output').exists()


def test_sync_rejects_overlapping_roots(inputs):
    from harness.engine_sync.sync import synchronize
    engine, hermes = inputs
    with pytest.raises(ValueError, match='separate'):
        synchronize(engine, engine, apply=True)


def test_sync_dirty_source_guard(tmp_path, monkeypatch):
    from harness.engine_sync import sync
    def git_read(root, *args):
        if args == ('rev-parse', 'HEAD'):
            return sync.UPSTREAM_COMMIT.encode()
        assert args == ('status', '--porcelain', '--untracked-files=no')
        return b' M file.py'
    monkeypatch.setattr(sync, '_git', git_read)
    with pytest.raises(ValueError, match='match the commit'):
        sync.tracked_paths(tmp_path)
