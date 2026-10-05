"""Numeric contracts, independent of model wording and presentation previews."""
from collections import defaultdict
import json


def metric_metadata(metric):
    definitions = {
        'pass_rate': {'unit': 'percent', 'numerator': 'A-bin die observations',
                      'denominator': 'all MAP entries in the selected measurement', 'direction': 'higher_is_better',
                      'source': 'LANGGRAPH_DATA.MAP_VAL_JSON'},
        'parameter': {'unit': 'source_value_unit_unspecified', 'numerator': 'VALUE',
                      'denominator': None, 'direction': 'parameter_specific',
                      'source': 'DF_DIE_TO_WF_YLD.VALUE'},
        'gms': {'unit': 'percent', 'direction': 'source_metric_specific',
                'source': 'DF_GMS_YIELD_WEEKLY', 'denominator': 'source aggregate; not reconstructed'},
    }
    return dict(definitions[metric])


def wafer_pass_metric(map_json, target_bin=None, bin_type='left_bin'):
    raw = json.loads(map_json) if isinstance(map_json, str) else map_json
    index = 2 if bin_type == 'left_bin' else 3
    entries = raw['MAP']
    bins = []
    for entry in entries:
        parts = entry.split(',')
        if len(parts) <= index or not parts[index]:
            raise ValueError('Map contains a die without the selected bin value')
        bins.append(parts[index])
    passed = sum(b != target_bin if target_bin is not None else b == 'A' for b in bins)
    return {'value': 100 * passed / len(bins) if bins else None,
            'pass_count': passed, 'die_count': len(bins)}


def select_measurements(rows, measurement_policy, time_key='end_tm'):
    """Latest means within this query's period. Timestamp ties remain explicit rows."""
    if measurement_policy == 'all_measurements':
        return list(rows)
    if measurement_policy != 'latest_per_wafer':
        raise ValueError('Unsupported measurement_policy')
    groups = defaultdict(list)
    for row in rows:
        if not row.get(time_key):
            raise ValueError('Latest measurement policy requires a measurement timestamp')
        groups[(row['lot_id'], row['wf_id'])].append(row)
    selected = []
    for records in groups.values():
        latest = max(str(row[time_key]) for row in records)
        selected.extend(row for row in records if str(row[time_key]) == latest)
    return selected


def rank_wafers(rows, periods, descending=False):
    """Dense ranks over a complete input; retain null measurements and empty periods."""
    groups = defaultdict(list)
    for row in rows:
        groups[row['period']].append(row)
    result = []
    for period in periods:
        values = groups[period]
        ranks = {v: i + 1 for i, v in enumerate(sorted({r['value'] for r in values if r['value'] is not None}, reverse=descending))}
        ranked = [{**row, 'rank': ranks.get(row['value'])} for row in values]
        result.extend(sorted(ranked, key=lambda r: (r['rank'] is None, r['rank'] or 0, r['wafer_id'])))
        if not values:
            result.append({'period': period, 'wafer_id': None, 'value': None, 'rank': None, 'measurement_count': 0})
    return result
