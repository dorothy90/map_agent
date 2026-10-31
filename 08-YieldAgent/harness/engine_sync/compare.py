"""Pure byte comparison, scope classification, integrity and report rendering."""
from collections import Counter
import difflib
import hashlib
import json
from pathlib import PurePosixPath

META = frozenset({'UPSTREAM.json', 'PATCHES.diff'})


def excluded(path):
    parts = PurePosixPath(path).parts
    return ('.git' in parts or '__pycache__' in parts or path.endswith('.pyc')
            or any(p == '.env' or p.startswith('.env.') for p in parts))


def comparable(path):
    return path not in META and not excluded(path)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def scope_rules(paths):
    paths = [PurePosixPath(p) for p in paths if comparable(p)]
    return {'top_level_directories': sorted({p.parts[0] for p in paths if len(p.parts) > 1}),
            'top_level_extensions': sorted({p.suffix for p in paths if p.suffix})}


def in_scope(path, scope):
    p = PurePosixPath(path)
    return (p.parts[0] in scope['top_level_directories'] if len(p.parts) > 1
            else bool(p.suffix) and p.suffix in scope['top_level_extensions'])


def line_counts(engine, hermes):
    """UTF-8 text only, <=1 MiB each; counts describe engine -> Hermes."""
    if max(len(engine), len(hermes)) > 1024 * 1024 or b'\0' in engine or b'\0' in hermes:
        return None
    try:
        old, new = engine.decode('utf-8').splitlines(), hermes.decode('utf-8').splitlines()
    except UnicodeDecodeError:
        return None
    added = removed = 0
    for tag, i, j, k, l in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ('replace', 'insert'):
            added += l - k
        if tag in ('replace', 'delete'):
            removed += j - i
    return {'added': added, 'removed': removed}


def classify(engine, hermes, upstream, scope=None):
    engine = {p: b for p, b in engine.items() if comparable(p)}
    hermes = {p: b for p, b in hermes.items() if comparable(p)}
    scope = scope if scope is not None else scope_rules(engine)
    identical, changed, added = [], [], []
    patched_local = []
    outside = Counter()
    for path in sorted(engine.keys() & hermes.keys()):
        eh, hh = digest(engine[path]), digest(hermes[path])
        if eh == hh:
            identical.append(path)
        elif eh == upstream['local_files'].get(path) and hh == upstream['files'].get(path):
            patched_local.append(path)
        else:
            pin = upstream['files'].get(path)
            changed.append({'path': path, 'engine_sha256': eh, 'hermes_sha256': hh,
                            'upstream_pin_sha256': pin, 'engine_matches_pin': eh == pin,
                            'is_local_patch': path in upstream['local_files'],
                            'lines': line_counts(engine[path], hermes[path])})
    for path in sorted(hermes.keys() - engine.keys()):
        if in_scope(path, scope):
            added.append(path)
        else:
            outside[path.split('/')[0] if '/' in path else '.'] += 1
    return {'identical': identical, 'changed': changed, 'patched_local': patched_local,
            'added': {'in_scope': added, 'out_of_scope': {
                'count': sum(outside.values()), 'by_top_level': dict(sorted(outside.items()))}},
            'deleted': sorted(engine.keys() - hermes.keys())}


def integrity(hashes, upstream, limit=100):
    expected = {**upstream['files'], **upstream['local_files']}
    expected = {p: h for p, h in expected.items() if comparable(p)}
    mismatches = sorted(p for p in hashes.keys() & expected.keys() if hashes[p] != expected[p])
    missing = sorted(expected.keys() - hashes.keys())
    local = {p: {'expected_sha256': h, 'actual_sha256': hashes.get(p),
                 'matches': hashes.get(p) == h}
             for p, h in sorted(upstream['local_files'].items())}
    return {'matched': sum(hashes[p] == expected[p] for p in hashes.keys() & expected.keys()),
            'mismatched': len(mismatches), 'unlisted': len(hashes.keys() - expected.keys()),
            'mismatch_paths': mismatches[:limit], 'mismatch_paths_omitted': max(0, len(mismatches) - limit),
            'missing': len(missing), 'missing_paths': missing[:limit],
            'missing_paths_omitted': max(0, len(missing) - limit), 'local_files': local,
            'policy': 'local_files overrides files (post-patch hash)'}


def make_report(engine, hermes, upstream, source, patches, expected_commit, scope=None):
    comparison = classify(engine, hermes, upstream, scope=scope)
    hashes = {p: digest(b) for p, b in sorted(engine.items()) if comparable(p)}
    return {'schema_version': 1, 'expected_commit': expected_commit,
            'upstream_pin': upstream['commit'], 'repository': upstream.get('repository'),
            'adaptations': upstream.get('adaptations', []), 'hermes': source,
            'scope': scope if scope is not None else scope_rules(engine), 'engine_meta': sorted(META & engine.keys()),
            'exclusions': ['__pycache__/**', '*.pyc', '.git/**', '.env and .env.* (never read)',
                           'UPSTREAM.json and PATCHES.diff: engine_meta, not compared'],
            'line_count_direction': 'engine -> hermes; UTF-8 text <= 1 MiB, otherwise null',
            'engine_hashes': hashes, 'comparison': comparison,
            'summary': {**({'patched_local': len(comparison['patched_local'])} if comparison['patched_local'] else {}), 'identical': len(comparison['identical']), 'changed': len(comparison['changed']),
                        'added_in_scope': len(comparison['added']['in_scope']),
                        'added_out_of_scope': comparison['added']['out_of_scope']['count'],
                        'deleted': len(comparison['deleted'])},
            'integrity': integrity(hashes, upstream), 'patches': patches,
            'conflicts': [{'path': p['path'], 'file_applies': p['applies'],
                           'hunks': [h['header'] for h in p['hunks'] if not h['applies']]}
                          for p in patches if not p['applies'] or any(not h['applies'] for h in p['hunks'])]}


def json_bytes(report):
    return (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def render_markdown(report, top_n=20):
    summary, check = report['summary'], report['integrity']
    lines = ['# 엔진 동기화 비교 보고서', '', '## 요약', '',
             f"- Hermes 커밋: `{report['hermes']['commit']}`; clean: `{report['hermes']['clean']}`",
             f"- 기존 UPSTREAM pin: `{report['upstream_pin']}`", '', '| 분류 | 개수 |', '|---|---:|']
    for key, label in [('identical', '동일'), ('changed', '변경'), ('added_in_scope', '추가 in-scope'),
                       ('added_out_of_scope', '추가 out-of-scope'), ('deleted', '삭제')]:
        lines.append(f'| {label} | {summary[key]} |')
    if summary.get('patched_local'):
        lines.append(f"| 패치 후 해시 일치 | {summary['patched_local']} |")
    lines += ['', '추가는 Hermes에만 있는 파일, 삭제는 엔진에만 있는 파일입니다.',
              '범위: scope_5894e750.json이 있으면 PR-4a 범위를 고정 사용합니다. 없으면 입력 엔진의 폴더·확장자로 계산합니다.',
              '확장자 없는 새 최상위 파일은 out-of-scope입니다.',
              'out-of-scope는 전체 경로를 나열하지 않고 최상위 폴더별 개수만 기록합니다.', '',
              '| out-of-scope 폴더 (`.` = 최상위 파일) | 개수 |', '|---|---:|']
    lines += [f'| `{p}` | {n} |' for p, n in report['comparison']['added']['out_of_scope']['by_top_level'].items()]
    lines += ['', '## 패치 호환성', '', '| 파일 | 전체 적용 가능 | Hermes가 pin 원본과 동일 | 충돌 hunk |', '|---|---|---|---|']
    for p in report['patches']:
        conflicts = ', '.join(f"`{h['header']}`" for h in p['hunks'] if not h['applies']) or '없음'
        lines.append(f"| `{p['path']}` | {p['applies']} | {p['hermes_matches_pin']} | {conflicts} |")
        if p['missing_in_hermes']:
            lines.append(f"\n- `{p['path']}`: missing_in_hermes\n")
    lines += ['', '파일 전체 및 개별 hunk는 각각 변경 전 원본 복사본에서 `git apply --check -p1`로 검사했습니다.',
              '실제 적용은 수행하지 않았습니다. 개별 통과가 조합 적용 또는 런타임 호환성을 보장하지 않습니다.',
              '', '## 로컬 패치 파일', '', '| 파일 | 패치 후 기록 일치 | Hermes 대비 상태 |', '|---|---|---|']
    changed = {p['path'] for p in report['comparison']['changed']}
    deleted = set(report['comparison']['deleted'])
    for p, entry in check['local_files'].items():
        state = '엔진 누락' if entry['actual_sha256'] is None else ('패치 후 해시 일치' if p in report['comparison']['patched_local'] else '변경' if p in changed else '삭제' if p in deleted else '동일')
        lines.append(f"| `{p}` | {entry['matches']} | {state} |")
    lines += ['', f'## 변경 파일 상위 {top_n}개', '', '줄 수 방향: 현재 엔진 → Hermes. UTF-8 텍스트만 계산하며 1 MiB 초과/바이너리는 null.',
              '', '| 파일 | 추가 줄 | 제거 줄 |', '|---|---:|---:|']
    ranked = sorted(report['comparison']['changed'], key=lambda p: (
        -sum(p['lines'].values()) if p['lines'] is not None else 1, p['path']))
    for p in ranked[:top_n]:
        counts = p['lines'] or {'added': None, 'removed': None}
        lines.append(f"| `{p['path']}` | {counts['added']} | {counts['removed']} |")
    lines += ['', '## 무결성', '',
              f"- 일치 {check['matched']}, 불일치 {check['mismatched']}, 목록외 {check['unlisted']}, 기록 파일 누락 {check['missing']}",
              '- local_files 패치 후 해시를 우선하고, 나머지는 files 원본 해시와 비교합니다.',
              '- engine_matches_pin은 local_files가 아닌 files 원본 해시와 비교한 별도 값입니다.',
              f"- 불일치 경로: {', '.join(check['mismatch_paths']) or '없음'} (생략 {check['mismatch_paths_omitted']}개)",
              f"- 누락 경로: {', '.join(check['missing_paths']) or '없음'} (생략 {check['missing_paths_omitted']}개)",
              f"- 엔진 해시 {len(report['engine_hashes'])}개 전체: [JSON](report_{report['expected_commit']}.json)의 engine_hashes.",
              '- 제외: __pycache__, *.pyc, .git, .env/.env.*. UPSTREAM.json·PATCHES.diff는 engine_meta로 별도 표시.',
              '', '## 재현 명령', '', '저장소 08-YieldAgent 디렉터리에서:', '', '```sh',
              'PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync',
              'PYTHONDONTWRITEBYTECODE=1 ../.venv/bin/python -B -m harness.engine_sync --check-only', '```',
              '', '## 한계·미확인 사항', '',
              '- 두 커밋의 직접 diff가 아니라 현재 엔진 바이트와 검증한 Hermes 작업트리를 비교합니다.',
              '- pin 원본에 대한 판단은 UPSTREAM.json 해시에 의존합니다. 원본 패치 재구성이나 의미적 검증은 하지 않았습니다.',
              '- Git 입력은 추적 파일만 읽습니다. clean=false이면 보고서는 커밋 자체가 아닌 수정된 작업트리 상태를 반영합니다.',
              '- 일반 디렉터리 입력은 commit/clean=null이며 커밋 검증을 할 수 없습니다.',
              '- 제외한 환경 파일은 열람하지 않았습니다. 바이너리 판정은 NUL/UTF-8 해독 가능 여부를 사용합니다.',
              '- 소스·패치 내용은 출력하지 않습니다. DB·LLM·엔진 실행, 의존성 설치 및 실제 패치 적용은 범위 밖입니다.', '']
    return '\n'.join(lines)
