"""Wafer-map evidence contracts and rendering of saved verification results."""
import json
import math
from collections import defaultdict

FIXED_VERIFICATION_FAILURE = '최종 검증을 통과하지 못해 답변을 확정하지 못했습니다. 확인된 조회·계산 자료는 아래에 제공됩니다.'
WAFER_MAP_CONTRACT_VERSION = 'wafer-map-evidence/v1'
WAFER_MAP_REQUIRED_COLUMNS = {
    'wafer_map_manifest': (
        'contract_version', 'map_type', 'selection_id', 'grouping_mode', 'group_id',
        'member_count', 'measurement_policy', 'numerator_mode', 'numerator_bins_json',
        'source_result_ids_json',
    ),
    'wafer_map_members': (
        'contract_version', 'selection_id', 'group_id', 'source_result_id', 'lot_id',
        'wf_id', 'measurement_id',
    ),
    'wafer_map_cells': (
        'contract_version', 'selection_id', 'group_id', 'row', 'col', 'fail_count',
        'observation_count', 'fail_rate',
    ),
}
WAFER_MAP_TABLES = tuple(WAFER_MAP_REQUIRED_COLUMNS)


def normalize_wafer_map_tables(tables):
    candidates = defaultdict(list)
    for name, rows in tables.items():
        if name in WAFER_MAP_TABLES or not rows:
            continue
        matches = [canonical for canonical, columns in WAFER_MAP_REQUIRED_COLUMNS.items()
                   if all(set(columns).issubset(row) for row in rows)]
        if len(matches) == 1 and matches[0] not in tables:
            candidates[matches[0]].append(name)
    renamed = {names[0]: canonical for canonical, names in candidates.items() if len(names) == 1}
    return {renamed.get(name, name): rows for name, rows in tables.items()}, renamed


def _missing_table_issue(names):
    calls = '; '.join(f"emit_table({name}_df, name='{name}')" for name in names)
    return ('웨이퍼 맵 구조화 근거 표가 없습니다: ' + ', '.join(names)
            + '. 각 필수 열을 갖춘 실제 근거 DataFrame을 저장하세요: ' + calls
            + '. 후보 표가 여러 개이거나 열이 부족하면 자동으로 이름을 추정하지 않습니다. '
            'execute_code로 근거 표와 맵을 함께 다시 생성한 뒤 최종 답변을 제출하세요.')


def _contract_fail(*issues):
    return {'verdict': 'fail', 'issues': list(dict.fromkeys(
        issue for issue in issues if issue))[:20]}


def _json_list(value, field):
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        raise ValueError(field + ' must be a JSON array') from None
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise ValueError(field + ' must be a JSON string array')
    if len(parsed) != len(set(parsed)):
        raise ValueError(field + ' must not contain duplicates')
    return parsed


def _member_key(row):
    return (str(row['source_result_id']), str(row['lot_id']), int(row['wf_id']),
            str(row['measurement_id']))


def _coordinate(row):
    return int(row['row']), int(row['col'])


def _same_number(actual, expected):
    if expected is None:
        return actual is None or isinstance(actual, float) and math.isnan(actual)
    return (isinstance(actual, (int, float)) and not isinstance(actual, bool)
            and math.isfinite(float(actual)) and math.isclose(float(actual), expected,
                rel_tol=1e-9, abs_tol=1e-12))


def validate_wafer_map_contract(tables, source_rows_by_id):
    """Recalculate a structured wafer-map contract from normalized source rows."""
    missing = [name for name in WAFER_MAP_TABLES[:2] if name not in tables]
    if missing:
        return _contract_fail(_missing_table_issue(missing))
    manifests = tables['wafer_map_manifest']
    members = tables['wafer_map_members']
    if not manifests:
        return _contract_fail('wafer_map_manifest가 비어 있습니다.')
    issues = []
    manifest_by_group = {}
    selection_groups = defaultdict(list)
    selection_sources = defaultdict(set)
    required_manifest = set(WAFER_MAP_REQUIRED_COLUMNS['wafer_map_manifest'])
    for row in manifests:
        absent = required_manifest - set(row)
        if absent:
            issues.append('wafer_map_manifest 필드 누락: ' + ', '.join(sorted(absent)))
            continue
        selection, group = str(row['selection_id']), str(row['group_id'])
        key = selection, group
        if key in manifest_by_group:
            issues.append(f'중복 그룹 선언: {selection}/{group}')
            continue
        if row['contract_version'] != WAFER_MAP_CONTRACT_VERSION:
            issues.append(f'지원하지 않는 계약 버전: {row["contract_version"]}')
        if row['map_type'] not in ('cummap', 'binmap'):
            issues.append(f'지원하지 않는 map_type: {row["map_type"]}')
        if row['grouping_mode'] not in ('selection_set', 'explicit_groups'):
            issues.append(f'지원하지 않는 grouping_mode: {row["grouping_mode"]}')
        if not isinstance(row['measurement_policy'], str) or not row['measurement_policy'].strip():
            issues.append(f'측정/재측정 정책이 없습니다: {selection}/{group}')
        if row['numerator_mode'] not in ('defined_fail_bins', 'selected_bins'):
            issues.append(f'지원하지 않는 numerator_mode: {row["numerator_mode"]}')
        try:
            source_ids = _json_list(row['source_result_ids_json'], 'source_result_ids_json')
            numerator_bins = _json_list(row['numerator_bins_json'], 'numerator_bins_json')
        except ValueError as exc:
            issues.append(str(exc))
            source_ids, numerator_bins = [], []
        if not source_ids or any(source_id not in source_rows_by_id for source_id in source_ids):
            issues.append(f'원본에 없는 source_result_id: {selection}/{group}')
        if row['numerator_mode'] == 'selected_bins' and not numerator_bins:
            issues.append(f'selected_bins 분자 집합이 비어 있습니다: {selection}/{group}')
        try:
            member_count = int(row['member_count'])
            if isinstance(row['member_count'], bool) or member_count < 1:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(f'member_count가 올바르지 않습니다: {selection}/{group}')
            member_count = -1
        normalized = {**row, '_source_ids': source_ids, '_numerator_bins': set(numerator_bins),
                      '_member_count': member_count}
        manifest_by_group[key] = normalized
        selection_groups[selection].append(normalized)
        selection_sources[selection].update(source_ids)

    for selection, groups in selection_groups.items():
        modes = {row['grouping_mode'] for row in groups}
        if len(modes) != 1:
            issues.append(f'선택 집합의 grouping_mode가 일관되지 않습니다: {selection}')
        elif modes == {'selection_set'} and len(groups) != 1:
            issues.append(f'선택 집합과 명시 그룹이 혼합되었습니다: {selection}')

    required_member = set(WAFER_MAP_REQUIRED_COLUMNS['wafer_map_members'])
    members_by_group = defaultdict(list)
    seen_members = set()
    seen_selection_members = set()
    for row in members:
        absent = required_member - set(row)
        if absent:
            issues.append('wafer_map_members 필드 누락: ' + ', '.join(sorted(absent)))
            continue
        key = str(row['selection_id']), str(row['group_id'])
        if row['contract_version'] != WAFER_MAP_CONTRACT_VERSION or key not in manifest_by_group:
            issues.append(f'선언되지 않은 그룹 구성원: {key[0]}/{key[1]}')
            continue
        try:
            member_key = _member_key(row)
        except (TypeError, ValueError, KeyError):
            issues.append(f'올바르지 않은 그룹 구성원: {key[0]}/{key[1]}')
            continue
        unique = (*key, *member_key)
        partition_key = (key[0], *member_key)
        if unique in seen_members:
            issues.append(f'중복 그룹 구성원: {key[0]}/{key[1]}/{member_key[1:]}')
            continue
        if partition_key in seen_selection_members:
            issues.append(f'선택 구성원이 여러 그룹에 중복되었습니다: {key[0]}/{member_key[1:]}')
        seen_members.add(unique)
        seen_selection_members.add(partition_key)
        manifest = manifest_by_group[key]
        if member_key[0] not in manifest['_source_ids']:
            issues.append(f'그룹 원본 선언과 구성원이 다릅니다: {key[0]}/{key[1]}')
        members_by_group[key].append(member_key)

    source_measurements = {}
    for source_id, source_rows in source_rows_by_id.items():
        measurement_rows = defaultdict(list)
        seen_coordinates = set()
        for row in source_rows:
            try:
                member_key = (str(source_id), str(row['lot_id']), int(row['wf_id']),
                              str(row['measurement_id']))
                coordinate = _coordinate(row)
            except (TypeError, ValueError, KeyError):
                issues.append(f'원본 die 행 필드가 올바르지 않습니다: {source_id}')
                continue
            duplicate_key = member_key, coordinate
            if duplicate_key in seen_coordinates:
                issues.append(f'원본 measurement 좌표가 중복되었습니다: {member_key[1:]}/{coordinate}')
            seen_coordinates.add(duplicate_key)
            measurement_rows[member_key].append(row)
        source_measurements.update(measurement_rows)

    for key, manifest in manifest_by_group.items():
        declared = members_by_group.get(key, [])
        if len(declared) != manifest['_member_count']:
            issues.append(f'그룹 구성원 수가 선언과 다릅니다: {key[0]}/{key[1]}')
        represented_sources = {member[0] for member in declared}
        if represented_sources != set(manifest['_source_ids']):
            issues.append(f'그룹 source_result_id 구성에 누락/초과가 있습니다: {key[0]}/{key[1]}')
        for member_key in declared:
            if member_key not in source_measurements:
                issues.append(f'원본에 없는 그룹 구성원: {key[0]}/{key[1]}/{member_key[1:]}')

    if issues:
        return _contract_fail(*issues)

    cummap_groups = {key: row for key, row in manifest_by_group.items()
                     if row['map_type'] == 'cummap'}
    if not cummap_groups:
        return {'verdict': 'pass', 'issues': []}
    if 'wafer_map_cells' not in tables:
        return _contract_fail(_missing_table_issue(['wafer_map_cells']))

    expected = {}
    for key, manifest in cummap_groups.items():
        selection = key[0]
        coordinate_domain = {
            _coordinate(row)
            for source_id in selection_sources[selection]
            for row in source_rows_by_id[source_id]
        }
        observations = defaultdict(list)
        for member_key in members_by_group[key]:
            for row in source_measurements[member_key]:
                observations[_coordinate(row)].append(row)
        for coordinate in coordinate_domain:
            rows = observations.get(coordinate, [])
            denominator = len(rows)
            if manifest['numerator_mode'] == 'defined_fail_bins':
                numerator = sum(row.get('fail_bin_name') is not None for row in rows)
            else:
                numerator = sum(str(row.get('bin')) in manifest['_numerator_bins'] for row in rows)
            expected[(*key, *coordinate)] = (numerator, denominator,
                numerator / denominator if denominator else None)

    required_cell = set(WAFER_MAP_REQUIRED_COLUMNS['wafer_map_cells'])
    actual = {}
    for row in tables['wafer_map_cells']:
        absent = required_cell - set(row)
        if absent:
            issues.append('wafer_map_cells 필드 누락: ' + ', '.join(sorted(absent)))
            continue
        try:
            key = (str(row['selection_id']), str(row['group_id']), *_coordinate(row))
        except (TypeError, ValueError, KeyError):
            issues.append('wafer_map_cells 좌표가 올바르지 않습니다.')
            continue
        if row['contract_version'] != WAFER_MAP_CONTRACT_VERSION or key in actual:
            issues.append(f'중복 또는 버전 불일치 좌표 근거: {key}')
            continue
        actual[key] = row
    missing_cells = set(expected) - set(actual)
    extra_cells = set(actual) - set(expected)
    if missing_cells:
        issues.append(f'좌표별 집계 근거가 누락되었습니다: {len(missing_cells)}개')
    if extra_cells:
        issues.append(f'원본 범위 밖 좌표별 집계가 있습니다: {len(extra_cells)}개')
    for key in set(expected) & set(actual):
        numerator, denominator, rate = expected[key]
        row = actual[key]
        if (not _same_number(row['fail_count'], numerator)
                or not _same_number(row['observation_count'], denominator)
                or not _same_number(row['fail_rate'], rate)):
            issues.append(f'좌표별 fail_count/observation_count/fail_rate 불일치: {key}')
    return _contract_fail(*issues) if issues else {'verdict': 'pass', 'issues': []}


def candidate_artifact_references(answer):
    from markdown_it import MarkdownIt
    from urllib.parse import urlsplit, unquote

    from html import unescape
    from posixpath import normpath
    references = []
    def visit(tokens):
        link_depth = 0
        for token in tokens:
            if token.type in ('link_open', 'image'):
                href = token.attrGet('src' if token.type == 'image' else 'href') or ''
                parsed = urlsplit(href)
                path = normpath('/' + unquote(parsed.path).replace('\\', '/').lstrip('/'))
                if not parsed.scheme and not parsed.netloc and (path.startswith('/harness/artifacts')
                        or unquote(parsed.path).startswith('/harness/artifacts')):
                    references.append({'href': href, 'image': token.type == 'image'})
                elif parsed.fragment and '/harness/artifacts' in unquote(parsed.fragment):
                    references.append({'href': href, 'image': token.type == 'image', 'unsupported': True})
            if token.type == 'link_open':
                link_depth += 1
            elif token.type == 'link_close':
                link_depth -= 1
            elif token.type in ('html_inline', 'html_block') or (token.type == 'text' and not link_depth):
                if '/harness/artifacts' in unquote(unescape(token.content)):
                    references.append({'href': token.content, 'image': False, 'unsupported': True})
            if token.children and token.type != 'image':
                visit(token.children)
    visit(MarkdownIt('commonmark').parse(answer))
    return references


async def inspect_answer_artifacts(store, principal_id, session_id, answer):
    from urllib.parse import urlsplit, unquote
    from .artifact_delivery import artifact_url

    issues, selected, resolved = [], [], {}
    async def resolve(result_id):
        if result_id in resolved:
            return resolved[result_id]
        observation = await store.observation(principal_id, session_id, result_id)
        item = observation.model_dump(mode='json')
        if item['principal_id'] != principal_id or item['session_id'] != session_id:
            raise PermissionError('Result unavailable')
        if item['status'] not in ('success', 'partial', 'empty'):
            raise ValueError('Unusable artifact result')
        resolved[result_id] = item
        for source_id in item.get('source_result_ids', []):
            await resolve(source_id)
        return item

    for reference in candidate_artifact_references(answer):
        href = reference['href']
        try:
            if reference.get('unsupported'):
                raise ValueError('Use the supplied artifact Markdown reference')
            artifact_id = unquote(urlsplit(href).path.removeprefix('/harness/artifacts/'))
            if href != artifact_url(session_id, artifact_id):
                raise ValueError('Noncanonical artifact reference')
            ref, _ = await store.artifact(principal_id, session_id, artifact_id)
            item = await resolve(ref['result_id'])
            owned = next((entry for entry in item['artifact_refs']
                if entry.get('artifact_id') == artifact_id), None)
            if owned is None or ref.get('artifact_id') != artifact_id or owned.get('result_id', item['result_id']) != item['result_id']:
                raise ValueError('Artifact result mismatch')
            mime = str(ref.get('mime') or '').split(';', 1)[0].strip().lower()
            if not mime or mime != str(owned.get('mime') or '').split(';', 1)[0].strip().lower():
                raise ValueError('Artifact MIME mismatch')
            if reference['image'] and (not mime.startswith('image/') or mime == 'image/svg+xml'):
                raise ValueError('Artifact is not an inline image')
            if item['result_id'] not in selected:
                selected.append(item['result_id'])
        except Exception:
            issues.append('저장된 산출물 참조를 확인할 수 없습니다. 제공된 markdown 참조를 그대로 사용하세요: ' + href)
    return {'verdict': 'fail' if issues else 'pass', 'issues': issues,
        'result_ids': list(resolved), 'selected_result_ids': selected,
        'observations': list(resolved.values())}


async def inspect_wafer_map_contracts(store, principal_id, session_id, observations, *, selected_result_ids=None):
    """Validate selected plots, retaining latest-per-source behavior for old runs."""
    sources = {item['result_id']: item for item in observations
               if item.get('tool_name') == 'load_wafer_maps'}
    latest = {}
    index = {item['result_id']: item for item in observations}
    def map_sources(item):
        pending, seen = list(item.get('source_result_ids', [])), set()
        while pending:
            result_id = pending.pop()
            if result_id in seen:
                continue
            seen.add(result_id)
            pending.extend(index.get(result_id, {}).get('source_result_ids', []))
        return tuple(sorted(seen & set(sources)))
    for item in observations:
        if selected_result_ids is not None and item['result_id'] not in selected_result_ids:
            continue
        source_ids = (map_sources(item) if selected_result_ids is not None else
            tuple(sorted(set(item.get('source_result_ids', [])) & set(sources))))
        if item.get('tool_name') in {'execute_code', 'run_python'} and item.get('artifact_refs') and source_ids:
            key = item['result_id'] if selected_result_ids is not None else source_ids
            latest[key] = (source_ids, item)
    if not latest:
        return {'verdict': 'not_applicable', 'issues': [], 'result_ids': []}
    issues, result_ids, normalizations = [], [], []
    for source_ids, result in latest.values():
        result_ids.extend([result['result_id'], *source_ids])
        source_rows = {}
        tables = {}
        try:
            for source_id in source_ids:
                source_rows[source_id] = await store.rows(
                    principal_id, session_id, source_id, table_id='wafer_map_dies')
            for table in result.get('tables', []):
                tables[table['table_id']] = await store.rows(
                    principal_id, session_id, result['result_id'], table_id=table['table_id'])
        except Exception:
            issues.append(f'{result["result_id"]}: 웨이퍼 맵 원본/근거 표를 읽지 못했습니다.')
            continue
        tables, renamed = normalize_wafer_map_tables(tables)
        if renamed:
            normalizations.append({'result_id': result['result_id'], 'table_names': renamed})
        report = validate_wafer_map_contract(tables, source_rows)
        issues.extend(f'{result["result_id"]}: {issue}' for issue in report['issues'])
    result_ids = list(dict.fromkeys(result_ids))[:50]
    return {'verdict': 'fail' if issues else 'pass', 'issues': issues, 'result_ids': result_ids,
            **({'normalizations': normalizations} if normalizations else {})}


def enforce_wafer_map_contract_review(report, contract_review):
    """A deterministic source mismatch cannot be waived by a model pass."""
    if contract_review.get('verdict') != 'fail':
        return report
    return {**report, 'verdict': 'fail',
        'issues': list(dict.fromkeys([
            *report.get('issues', []), *contract_review.get('issues', [])]))[:20],
        'result_ids': list(dict.fromkeys([
            *report.get('result_ids', []), *contract_review.get('result_ids', [])]))[:50]}


def verification_failure_answer(answer, report):
    issues = '\n'.join('- ' + issue for issue in report['issues'])
    if report['verdict'] == 'fail' and isinstance(answer, str) and answer.strip():
        return answer + '\n\n---\n⚠️ 자동 검증에서 확인되지 않은 사항:\n' + issues
    if report['verdict'] == 'unknown' and isinstance(answer, str) and answer.strip():
        return answer + '\n\n---\n' + FIXED_VERIFICATION_FAILURE + '\n\n' + issues
    return FIXED_VERIFICATION_FAILURE + '\n\n' + issues
