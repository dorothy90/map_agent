"""Bind schema-validated MAP and process-history axes to each source measurement."""
from collections import defaultdict
from datetime import date
from functools import lru_cache
import hashlib
import json
import os
from typing import Literal

from pydantic import Field, model_validator

from ..types import Contract


class MatrixAxis(Contract):
    source: Literal['map', 'legend'] = Field(description='map = MAP measurement, legend = DF_WF_LEGEND process visit.')
    field: str = Field(min_length=1, max_length=128, description=(
        'Exact scalar column name from list_tables for the source. MAP.WF_ID is the wafer slot across LOTs.'))
    operation: str | None = Field(default=None, min_length=1, max_length=500, description=(
        'For legend only, the manufacturing process name or ID as requested. On no unique match check its process list.'))
    bucket: Literal['day', 'iso_week', 'month'] | None = Field(default=None, description=(
        'Optional time bucket on an explicit date/time field. MAP.END_TM uses the map measurement end, '
        'legend.END_TM uses the selected process visit end.'))

    @model_validator(mode='after')
    def operation_scope(self):
        if (self.source == 'legend') != bool(self.operation and self.operation.strip()):
            raise ValueError('An operation is required only for a legend axis.')
        return self


class MapMatrix(Contract):
    rows: MatrixAxis
    columns: MatrixAxis


_TABLES = {'map': 'MAP', 'legend': 'DF_WF_LEGEND'}
_TIME_FORMATS = {'day': 'YYYY-MM-DD', 'iso_week': 'IYYY-"W"IW', 'month': 'YYYY-MM'}


def resolve_operation(cursor, requested, product):
    terms = 'AND LOT_CD = :lot_cd' if product else ''
    binds = {'lot_cd': product} if product else {}
    cursor.execute(f'''SELECT DISTINCT OPER_ID FROM DF_WF_LEGEND
        WHERE (OPER_ID = :requested_oper OR UPPER(OPER_DET_DESC) = UPPER(:requested_oper)) {terms}''',
        dict(binds, requested_oper=requested.strip()))
    operations = cursor.fetchmany(2)
    if len(operations) != 1 or not operations[0][0]:
        raise ValueError('Expected one matching DF_WF_LEGEND process. Specify its exact OPER_ID or name.')
    return operations[0][0]


def _kind(data_type):
    if data_type == 'DATE' or data_type.startswith('TIMESTAMP'):
        return 'time'
    if data_type in ('NUMBER', 'FLOAT', 'BINARY_FLOAT', 'BINARY_DOUBLE'):
        return 'number'
    if data_type in ('CHAR', 'VARCHAR2', 'NCHAR', 'NVARCHAR2'):
        return 'text'
    return None


def resolve_matrix(cursor, args):
    cursor.execute("""SELECT table_name,column_name,data_type FROM USER_TAB_COLUMNS
        WHERE table_name IN ('MAP','DF_WF_LEGEND') ORDER BY column_id""")
    columns = { (table, name): _kind(typ) for table, name, typ in cursor.fetchall() if _kind(typ) }
    operations = {}
    matrix = {}
    for role in ('rows', 'columns'):
        axis = getattr(args.aggregate.matrix, role)
        field = axis.field.strip().upper()
        table = _TABLES[axis.source]
        kind = columns.get((table, field))
        if not kind:
            available = ', '.join(name for owner, name in columns if table == owner)
            raise ValueError(f'Unsupported {table} axis {field!r}. Eligible fields: {available}.')
        legacy_time_text = (axis.source, field, kind) == ('legend', 'END_TM', 'text')
        if legacy_time_text:
            kind = 'time'
        if axis.bucket and kind != 'time':
            raise ValueError(f'Time bucket requires a date/time axis, got {table}.{field}.')
        descriptor = dict(source=axis.source, field=field, value_type=kind)
        if legacy_time_text:
            descriptor['legacy_time_text'] = True
        if axis.operation:
            if axis.operation not in operations:
                operations[axis.operation] = resolve_operation(cursor, axis.operation, args.lot_cd)
            descriptor.update(operation=operations[axis.operation], operation_name=axis.operation)
        if axis.bucket:
            descriptor['bucket'] = axis.bucket
        matrix[role] = descriptor
    if _axis_identity(matrix['rows']) == _axis_identity(matrix['columns']):
        raise ValueError('Choose two distinct axes.')
    return matrix


def _axis_identity(axis):
    return (axis['source'], axis['field'], axis.get('operation', ''), axis.get('bucket', ''), axis['value_type'])


def _expression(alias, axis):
    field = axis['field'].replace('"', '""')
    expr = f'{alias}."{field}"'
    if axis['source'] == 'legend' and axis['field'] == 'END_TM' and axis.get('legacy_time_text'):
        expr = f"""TO_DATE({expr} DEFAULT NULL ON CONVERSION ERROR, 'YYYY-MM-DD HH24:MI:SS')"""
    if axis.get('bucket'):
        expr = f"""TO_CHAR({expr}, '{_TIME_FORMATS[axis["bucket"]]}')"""
    return expr


def matrix_sql(base, binds, matrix):
    values = {}
    by_operation = defaultdict(list)
    for key, name in [('rows', 'row_value'), ('columns', 'column_value')]:
        axis = matrix[key]
        if axis['source'] == 'legend':
            by_operation[axis['operation']].append((name, axis))
        else:
            values[name] = _expression('m', axis)
    joins, no_history, conflicts, missing = [], [], [], []
    for i, (operation, axes) in enumerate(by_operation.items()):
        alias = f'l{i}'
        bind = f'matrix_oper_{i}'
        binds[bind] = operation
        product = 'AND h.LOT_CD = :lot_cd' if 'lot_cd' in binds else ''
        distinct = ', '.join(f'{_expression("h", axis)} AS {name}' for name, axis in axes)
        present = ' AND '.join(f'{name} IS NOT NULL' for name, _ in axes)
        # MIN is read only when the complete distinct tuple is unique.
        picked = ', '.join(f'MIN({name}) AS {name}' for name, _ in axes)
        joins.append(f'''LEFT JOIN (
            SELECT lot_cd, lot_id, wf_id, {picked}, COUNT(*) AS visit_tuples,
                   COUNT(CASE WHEN {present} THEN 1 END) AS complete_tuples
            FROM (SELECT DISTINCT h.lot_cd,h.lot_id,h.wf_id,{distinct}
                  FROM DF_WF_LEGEND h WHERE h.OPER_ID=:{bind} {product})
            GROUP BY lot_cd,lot_id,wf_id
        ) {alias} ON {alias}.lot_cd=m.lot_cd AND {alias}.lot_id=m.lot_id AND {alias}.wf_id=TO_CHAR(m.wf_id)''')
        no_history.append(f'{alias}.visit_tuples IS NULL')
        conflicts.append(f'{alias}.complete_tuples > 1')
        missing.append(f'{alias}.visit_tuples > {alias}.complete_tuples')
        for name, _ in axes:
            values[name] = f'{alias}.{name}'
    missing.extend(f'{expr} IS NULL' for expr in values.values())
    cases = [(no_history, 'missing_history'), (conflicts, 'conflict'), (missing, 'missing_value')]
    state = 'CASE ' + ' '.join(f"WHEN {' OR '.join(terms)} THEN '{result}'" for terms, result in cases if terms) + ' END'
    return f'''SELECT m.LOT_CD, m.OPER_DET_DESC AS oper, m.LOT_ID, m.WF_ID, m.START_TM, m.END_TM,
        {values["row_value"]} AS row_value, {values["column_value"]} AS column_value,
        {state} AS group_issue, m.MAP_VAL_JSON FROM {base} {" ".join(joins)}''', binds


def matrix_grouping(matrix):
    axes = [_axis_identity(matrix[key]) for key in ('rows', 'columns')]

    @lru_cache(maxsize=8192)
    def identify(row_value, column_value):
        values = [v.isoformat() if isinstance(v, date) else v for v in (row_value, column_value)]
        key = json.dumps(sorted(zip(axes, values)), ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(key.encode()).hexdigest(), values

    return identify


def coordinate_budget():
    setting = 'HARNESS_MAP_MATRIX_MAX_COORDINATES'
    try:
        budget = int(os.environ.get(setting, '500000'))
    except ValueError:
        budget = 0
    if budget < 1:
        raise ValueError(f'{setting} must be a positive integer.')
    return budget
