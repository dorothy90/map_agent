"""Request-local storage for exact matrix counts across arbitrary source order."""
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

from ..native_verification import WAFER_MAP_CONTRACT_VERSION
from ..types import ResultTable, StreamedResultTable
from .map_matrix import coordinate_budget, matrix_grouping
from .registry import ToolResult


class CoordinateCounts:
    def __init__(self, connection, watermark):
        self.db = connection
        self.watermark = watermark
        self.buffer = defaultdict(dict)
        self.size = 0
        self.spills = 0

    def add(self, group, observations, product_test, is_fail):
        counts = self.buffer[group]
        for y, x, left, right in observations:
            key = y, x
            count = counts.get(key)
            if count is None:
                if self.size >= self.watermark:
                    self.flush()
                    counts = self.buffer[group]
                count = counts[key] = [0, 0]
                self.size += 1
            count[0] += 1
            count[1] += is_fail(product_test, left + right)

    def flush(self):
        with self.db:
            self.db.executemany(
                """INSERT INTO counts VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(g, y, x) DO UPDATE SET n=n+excluded.n, f=f+excluded.f""",
                ((g, y, x, n, f) for g, counts in self.buffer.items()
                 for (y, x), (n, f) in counts.items()))
        self.buffer.clear()
        self.size = 0
        self.spills += 1

    def group_counts(self, group):
        if self.spills:
            return self.db.execute("SELECT y,x,n,f FROM counts WHERE g=? ORDER BY y,x", (group,))
        return ((y, x, n, f) for (y, x), (n, f) in sorted(self.buffer.get(group, {}).items()))


def _output_table(name, rows, directory):
    if directory is None:
        return ResultTable(table_id=name, title=name, complete=True, rows=list(rows))
    path = Path(directory) / (name + '.jsonl')
    digest = hashlib.sha256()
    count, preview = 0, []
    with path.open('xb') as stream:
        for row in rows:
            line = json.dumps(row, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode() + b'\n'
            stream.write(line)
            digest.update(line)
            count += 1
            if count <= 50:
                preview.append(row)
    return StreamedResultTable(table_id=name, title=name, complete=True, rows=preview,
                               total_rows=count, source=path, source_digest=digest.hexdigest())


def summarize_matrix(rows, spec, is_fail, source_id, matrix, lot_ids, wf_ids, directory, render):
    from .load_wafer_maps import _measurement_id, _parse_payload

    identify = matrix_grouping(matrix)
    # The temporary DB belongs to this attempt. An exception discards both the ledger and counts.
    with TemporaryDirectory(dir=directory) as tmp, closing(sqlite3.connect(Path(tmp) / 'matrix.db')) as db:
        db.executescript("""
            CREATE TABLE measurements (
                id TEXT PRIMARY KEY, product TEXT, lot TEXT, wafer INTEGER, g INTEGER, issue TEXT
            );
            CREATE TABLE counts (g INTEGER, y INTEGER, x INTEGER, n INTEGER, f INTEGER,
                                 PRIMARY KEY (g,y,x)) WITHOUT ROWID;
        """)
        counts = CoordinateCounts(db, coordinate_budget())
        groups = {}
        duplicates = 0
        for row in rows:
            canonical, observations = _parse_payload(row['map_val_json'])
            identity = _measurement_id(row, canonical)
            issue, g = row['group_issue'], None
            if not issue:
                group_id, values = identify(row['row_value'], row['column_value'])
                if group_id not in groups:
                    groups[group_id] = (len(groups), values)
                g = groups[group_id][0]
            product, lot, wafer = row['lot_cd'], row['lot_id'], int(row['wf_id'])
            inserted = db.execute("INSERT OR IGNORE INTO measurements VALUES (?,?,?,?,?,?)",
                                  (identity, product, lot, wafer, g, issue)).rowcount
            if not inserted:
                if db.execute("SELECT g,issue FROM measurements WHERE id=?", (identity,)).fetchone() != (g, issue):
                    raise ValueError('Rows for the same MAP measurement disagree on its matrix axes. '
                                     'Check the source rows before assigning this measurement to a group.')
                duplicates += 1
                continue
            if not issue:
                counts.add(g, observations, (product, row['oper']), is_fail)

        if counts.spills and counts.size:
            counts.flush()
        evidence = dict(contract_version=WAFER_MAP_CONTRACT_VERSION, selection_id=source_id)
        def group_cells(group_id):
            for y, x, n, f in counts.group_counts(groups[group_id][0]):
                yield dict(**evidence, group_id=group_id, row=y, col=x,
                           observation_count=n, fail_count=f, fail_rate=f/n)

        missing_history, missing_value, conflicts, included, total = db.execute("""
            SELECT COUNT(CASE WHEN issue='missing_history' THEN 1 END),
                   COUNT(CASE WHEN issue='missing_value' THEN 1 END),
                   COUNT(CASE WHEN issue='conflict' THEN 1 END), COUNT(g), COUNT(*)
            FROM measurements""").fetchone()
        wafer_count = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT product,lot,wafer FROM measurements)").fetchone()[0]
        included_wafers = db.execute("SELECT COUNT(*) FROM (SELECT DISTINCT product,lot,wafer FROM measurements WHERE g IS NOT NULL)").fetchone()[0]
        group_sizes = {g: (nw, nm) for g, nw, nm in db.execute("""
            SELECT g,COUNT(*),SUM(n) FROM (SELECT g,product,lot,wafer,COUNT(*) n FROM measurements
                WHERE g IS NOT NULL GROUP BY g,product,lot,wafer) GROUP BY g""")}
        wanted_lots, wanted_wafers = set(lot_ids), set(wf_ids)
        missing_pairs = {(lot, wafer) for lot in lot_ids for wafer in wf_ids}
        if wanted_lots or wanted_wafers:
            for lot, wafer in db.execute("SELECT DISTINCT lot,wafer FROM measurements"):
                wanted_lots.discard(lot)
                wanted_wafers.discard(wafer)
                missing_pairs.discard((lot, wafer))
        absent = dict(lot_ids=sorted(wanted_lots), wf_ids=sorted(wanted_wafers),
                      lot_wafers=[list(pair) for pair in sorted(missing_pairs)])
        group_rows, manifest = [], []
        def cells():
            for group_id, (g, values) in sorted(groups.items()):
                observed = failed = 0
                for cell in group_cells(group_id):
                    observed += cell['observation_count']
                    failed += cell['fail_count']
                    yield cell
                nw, nm = group_sizes[g]
                group_rows.append(dict(group_id=group_id, row_value=values[0], column_value=values[1],
                                       wafer_count=nw, measurement_count=nm, observation_count=observed,
                                       fail_count=failed, fail_rate=failed/observed if observed else None))
                manifest.append(dict(**evidence, map_type='cummap', group_id=group_id, member_count=nm,
                    grouping_mode='explicit_groups', measurement_policy='All distinct MAP measurements, including retests',
                    numerator_mode='selected_bins' if spec.selected_bins else 'defined_fail_bins',
                    numerator_bins_json=json.dumps(sorted(set(spec.selected_bins))), source_result_ids_json=json.dumps([source_id])))

        group_ids = {g: key for key, (g, _) in groups.items()}
        members = (dict(**evidence, group_id=group_ids[g], source_result_id=source_id,
                        lot_id=lot, wf_id=wafer, measurement_id=identity)
                   for g, lot, wafer, identity in db.execute("SELECT g,lot,wafer,id FROM measurements WHERE g IS NOT NULL ORDER BY rowid"))
        cell_table = _output_table('wafer_map_cells', cells(), directory)
        member_table = _output_table('wafer_map_members', members, directory)
        missing = missing_history + missing_value
        facts = dict(missing_requests=absent, source_exhausted=True, grouping_complete=not (missing or conflicts),
                     group_by=spec.group_by, group_oper=spec.group_oper, matrix=matrix, wafer_count=wafer_count,
                     measurement_count=total, included_measurement_count=included, included_wafer_count=included_wafers,
                     missing_group_measurements=missing, conflicting_group_measurements=conflicts,
                     missing_history_measurements=missing_history, missing_value_measurements=missing_value,
                     duplicate_measurement_count=duplicates, die_observations=sum(g['observation_count'] for g in group_rows),
                     row_count=len({g['row_value'] for g in group_rows}), column_count=len({g['column_value'] for g in group_rows}))
        tables = [ResultTable(table_id='wafer_map_groups', title='wafer_map_groups', rows=group_rows, complete=True),
                  cell_table, member_table,
                  ResultTable(table_id='wafer_map_manifest', title='wafer_map_manifest', rows=manifest, complete=True)]
        status = 'partial' if missing or conflicts or any(absent.values()) else ('success' if included else 'empty')
        summary = (f'Cummap: {len(group_rows)} groups, {included}/{total} measurements grouped; '
                   f'{missing} missing and {conflicts} ambiguous group assignments.')
        result = ToolResult(scope={'map_aggregate': facts}, tables=tables, status=status, summary=summary)
        if render:
            from .map_matrix_plot import matrix_artifact
            result.artifacts.append(matrix_artifact(group_rows, group_cells, matrix, facts, spool_dir=directory))
            result.summary = (f"Rendered interactive matrix: {facts['row_count']} {matrix['rows']['field']} rows × "
                              f"{facts['column_count']} {matrix['columns']['field']} columns. "
                              "Display the finished, zoomable map using the returned artifact_refs[0].markdown. "
                              + summary)
        return result
