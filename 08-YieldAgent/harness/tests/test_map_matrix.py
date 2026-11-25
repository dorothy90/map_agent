import json
import sqlite3

import pytest

from harness.tools.load_wafer_maps import LoadWaferMapsInput
from harness.tools.map_aggregate import CummapAggregation, _group_query, _summarize


def axis(source, field, **kwargs):
    return dict(source=source, field=field, **kwargs)


def arguments(rows, columns):
    return LoadWaferMapsInput(lot_cd="P", oper="TEST", all_time=True,
                              aggregate={"matrix": {"rows": rows, "columns": columns}})


def database():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.create_function("TO_CHAR", 1, str)
    db.executescript("""
        CREATE TABLE USER_TAB_COLUMNS (table_name, column_name, data_type);
        INSERT INTO USER_TAB_COLUMNS VALUES ('DF_WF_LEGEND','EQP_ID','VARCHAR2'),
                                            ('DF_WF_LEGEND','PORT_NO','VARCHAR2'),
                                            ('MAP','WF_ID','NUMBER'), ('MAP','MAP_VAL_JSON','CLOB');
        ALTER TABLE USER_TAB_COLUMNS ADD column_id INTEGER;
        CREATE TABLE MAP (lot_cd,oper_det_desc,lot_id,wf_id,start_tm,end_tm,map_val_json);
        CREATE TABLE DF_WF_LEGEND (lot_cd,lot_id,wf_id,oper_id,oper_det_desc,eqp_id,port_no);
        INSERT INTO DF_WF_LEGEND VALUES ('P','L','1','01','V','A','2'), ('P','L','1','01','V','B','1'),
                                        ('P','L','2','01','V','A','2'), ('P','L','2','01','V','A','2'),
                                        ('P','L','3','01','V',NULL,'1'),
                                        ('P','L','2','02','Q','other','9');
    """)
    payload = json.dumps({"MAP": ["0,0,?,?"]})
    db.executemany("INSERT INTO MAP VALUES ('P','TEST','L',?,1,?,?)",
                   [(1, 1, payload), (2, 1, payload), (2, 1, payload), (2, 2, payload),
                    (3, 1, payload), (4, 1, payload)])
    return db


def result(db, rows, columns, **options):
    args = arguments(rows, columns)
    from harness.tools.map_matrix import resolve_matrix
    cursor = db.cursor()
    matrix = resolve_matrix(cursor, args)
    sql, binds = _group_query(cursor, args, matrix)
    rows = [dict(row) for row in cursor.execute(sql, binds)]
    return _summarize(rows, args.aggregate, {}, "source-id", matrix=matrix, **options)


def test_joint_visits_nulls_conflicts_duplicates_zero_and_transpose():
    db = database()
    equipment = axis("legend", "eqp_id", operation="V")
    port = axis("legend", "PORT_NO", operation="01")
    original = result(db, equipment, port)
    swapped = result(db, port, equipment)
    groups, cells, members, _ = [t.rows for t in original.tables]
    assert [(g["row_value"], g["column_value"], g["measurement_count"]) for g in groups] == [("A", "2", 2)]
    scope = original.scope["map_aggregate"]
    assert (scope["missing_history_measurements"], scope["missing_value_measurements"],
            scope["conflicting_group_measurements"],scope["duplicate_measurement_count"]) == (1, 1, 1, 1)
    assert cells[0]["observation_count"] == 2 and cells[0]["fail_count"] == 0
    transposed, scells, smembers, _ = [t.rows for t in swapped.tables]
    assert [(g["row_value"], g["column_value"], g["group_id"]) for g in transposed] == [("2", "A", groups[0]["group_id"])]
    assert cells == scells and members == smembers


def test_distinct_operation_and_map_source():
    db = database()
    eqp = axis("legend", "EQP_ID", operation="V")
    other = axis("legend","PORT_NO", operation="Q")
    assert result(db, eqp, other).tables[0].rows[0]["column_value"] == "9"
    slot = axis("map", "WF_ID")
    assert result(db, other, slot).tables[0].rows[0]["column_value"] == 2
    for invalid in ("MAP_VAL_JSON", "WF_ID) OR 1=1", "ZZZ"):
        with pytest.raises(ValueError, match="WF_ID"):
            result(db, other, axis("map", invalid))
    with pytest.raises(ValueError, match="bucket"):
        result(db, other, axis("map","WF_ID", bucket="month"))


def test_matrix_schema_excludes_competing_group_and_validates_source():
    matrix = {"rows": axis("map","LOT_CD"), "columns": axis("map","WF_ID")}
    with pytest.raises(ValueError):
        CummapAggregation(group_by="all", matrix=matrix)
    for invalid in (axis("map","WF_ID", operation="V"), axis("legend","EQP_ID")):
        with pytest.raises(ValueError):
            CummapAggregation(matrix=dict(matrix, rows=invalid))
    args = arguments(**matrix)
    assert LoadWaferMapsInput.model_validate(args.model_dump(mode="json")) == args


def test_duplicate_map_identity_with_different_axis_value_cannot_silently_choose_first():
    db = database()
    db.execute("ALTER TABLE MAP ADD fab_id")
    db.execute("INSERT INTO USER_TAB_COLUMNS VALUES ('MAP','FAB_ID','VARCHAR2',10)")
    db.execute("UPDATE MAP SET fab_id='F1'")
    db.execute("UPDATE MAP SET fab_id='F2' WHERE rowid=3")
    with pytest.raises(ValueError, match='same MAP measurement'):
        result(db, axis("map","FAB_ID"), axis("map","WF_ID"))


def test_matrix_spills_without_losing_counts_across_flushes(monkeypatch):
    monkeypatch.setenv("HARNESS_MAP_MATRIX_MAX_COORDINATES", "1")
    db = database()
    request_axes = (axis("legend","PORT_NO", operation="Q"), axis("map","WF_ID"))
    good = result(db, *request_axes)
    assert len(good.tables[1].rows) == 1
    assert good.tables[1].rows[0]["observation_count"] == 2
    db.execute("UPDATE MAP SET map_val_json=? WHERE wf_id=2",
               (json.dumps({"MAP": ["0,0,?,?", "0,1,?,?"]}),))
    out = result(db, *request_axes)
    assert [(cell['observation_count'], cell['fail_count']) for cell in out.tables[1].rows] == [(2, 0), (2, 0)]
    assert out.scope['map_aggregate']['duplicate_measurement_count'] == 1


def test_matrix_spool_preserves_full_coordinate_table(tmp_path):
    import hashlib
    db = database()
    db.execute("UPDATE MAP SET map_val_json=? WHERE wf_id=2",
               (json.dumps({"MAP": [f'0,{i},?,?' for i in range(100)]}),))
    request_axes = (axis("legend","PORT_NO", operation="Q"), axis("map","WF_ID"))
    cells = result(db, *request_axes).tables[1].rows
    streamed = result(db, *request_axes, spool_dir=tmp_path).tables[1]
    content = streamed.source.read_bytes()
    assert streamed.source_digest == hashlib.sha256(content).hexdigest()
    assert streamed.total_rows == len(cells) == 100
    assert streamed.rows == cells[:50]
    assert [json.loads(line) for line in content.splitlines()] == cells
