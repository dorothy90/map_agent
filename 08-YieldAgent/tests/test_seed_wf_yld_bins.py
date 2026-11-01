from decimal import Decimal
from datetime import date
import re
from types import SimpleNamespace

import pytest

from seed_wf_yld_bins import (
    _metadata_changes,
    MetadataChanges,
    Metadata,
    SeedPlan,
    _unrelated_rows_snapshot,
    _verify_generated_counts,
    compile_patterns,
    compute_ratios,
    ensure_backup,
    load_catalog_items,
    load_wafer_metadata,
    match_process,
    parse_map,
    plan_seed,
    replace_rows,
    verify_replacement_coverage,
    verify_unrelated_rows,
    verify_sample,
)


pytestmark = pytest.mark.no_server


def test_compute_ratios_selects_one_side_and_emits_zero_bins():
    pairs = [("?", "?"), ("B", "?"), ("?", "V"), ("B", "V"),
             ("V", "B"), ("B", "B"), (" ", "B")]
    patterns = [("BVDS(B)", re.compile(r"^(B.|.B)$")),
                ("VTH(V)", re.compile(r"^(V.|.V)$")),
                ("DDA(d)", re.compile(r"^dd$"))]

    result = compute_ratios(pairs, patterns, "?", "PT1H")

    assert result == {
        "PT1H": Decimal("0.142857142857"),
        "BVDS(B)": Decimal("0.571428571429"),
        "VTH(V)": Decimal("0.285714285714"),
        "DDA(d)": Decimal("0E-12"),
    }


def test_compute_ratios_rejects_ambiguous_selected_character():
    pairs = [("A", "B")]
    patterns = [
        ("LEFT_A", re.compile(r"^AA$")),
        ("ANY_A", re.compile(r"^AA$")),
    ]
    with pytest.raises(ValueError, match="ambiguous"):
        compute_ratios(pairs, patterns, "?", "PT1C")


def test_compute_ratios_counts_double_side_match_once_and_rounds_half_even():
    pairs = [("A", "A"), ("B", "B"), ("C", "C")]
    patterns = [(name, re.compile(f"^{name}{name}$")) for name in "ABC"]

    result = compute_ratios(pairs, patterns, "?", "PT1H")

    assert result["A"] == Decimal("0.333333333333")


def test_compute_ratios_rounds_half_even_at_twelve_places():
    pairs = [("B", "?")] + [("?", "?")] * 8191
    result = compute_ratios(pairs, [("BVDS(B)", re.compile("^BB$"))], "?", "PT1H")
    assert result["BVDS(B)"] == Decimal("0.000122070312")


@pytest.mark.parametrize("pairs,patterns,message", [
    ([(" ", "?")], [], "fail character"),
    ([("B", "V")], [("V", re.compile("^VV$"))], "missing"),
])
def test_compute_ratios_rejects_unclassified_die(pairs, patterns, message):
    with pytest.raises(ValueError, match=message):
        compute_ratios(pairs, patterns, "?", "PT1H")


def test_compute_ratios_rejects_empty_map():
    with pytest.raises(ValueError, match="empty"):
        compute_ratios([], [], "?", "PT1H")


def test_metadata_changes_rename_add_and_keep_existing_destination():
    catalog = [
        ("P", "PT1H", "o", "OLD", 7),
        ("P", "PT1H", "o", "ITEM", 2),
        ("P", "PT1C", "o", "ITEM", 1),
        ("Q", "PT1H", "o", "PT1H", 3),
        ("Q", "PT1H", "o", "OLD", 4),
    ]

    changes = _metadata_changes(catalog, {"OLD"}, {("P", "PT1H"), ("P", "PT1C"),
                                                   ("Q", "PT1H")})

    assert changes.renames == [("PT1H", "P", "PT1H", "OLD", 7)]
    assert changes.inserts == [("P", "PT1C", "o", "PT1C", 2)]
    assert changes.deletes == [
        ("P", "PT1H", "OLD"),
        ("Q", "PT1H", "OLD"),
    ]


def test_metadata_changes_are_idempotent_after_first_result():
    catalog = [
        ("P", "PT1H", "o", "PT1H", 7),
        ("P", "PT1H", "o", "ITEM", 2),
        ("P", "PT1C", "o", "PT1C", 2),
        ("P", "PT1C", "o", "ITEM", 1),
    ]

    changes = _metadata_changes(catalog, {"OLD"}, {("P", "PT1H"), ("P", "PT1C")})

    assert changes.renames == []
    assert changes.inserts == []
    assert changes.deletes == []


def test_metadata_changes_keep_same_named_item_in_another_process():
    catalog = [
        ("P", "PT1H", "o", "PT1C", 1),
        ("P", "PT1H", "o", "ITEM", 2),
        ("P", "PT1C", "o", "PT1C", 1),
    ]

    changes = _metadata_changes(catalog, {"OLD"}, {("P", "PT1H"), ("P", "PT1C")})

    assert changes.renames == []
    assert changes.inserts == [("P", "PT1H", "o", "PT1H", 3)]
    assert changes.deletes == []


def test_metadata_changes_leave_unrelated_processes_untouched_and_preserve_null_flag():
    catalog = [
        ("P", "PT1H", None, "ITEM", 1),
        ("P", "CFTA", "o", "CFTA_ITEM", 1),
        ("P", "CFTA", "o", "OLD", 2),
    ]

    changes = _metadata_changes(catalog, {"OLD"}, {("P", "PT1H")})

    assert changes.renames == []
    assert changes.inserts == [("P", "PT1H", None, "PT1H", 2)]
    assert changes.deletes == []


class RecordingCursor:
    def __init__(self, fetches=()):
        self.fetches = iter(fetches)
        self.calls = []
        self.rowcount = 0

    def execute(self, sql, binds=None):
        self.calls.append((sql, binds))

    def executemany(self, sql, rows):
        rows = list(rows)
        self.calls.append((sql, rows))
        self.rowcount = len(rows)

    def fetchone(self):
        return next(self.fetches)

    def close(self):
        pass

    def __iter__(self):
        return iter(getattr(self, "rows", []))


class RecordingConnection:
    def __init__(self, cursor):
        self.recording_cursor = cursor

    def cursor(self):
        return self.recording_cursor


def _empty_plan(**overrides):
    values = dict(rows=[], target_scopes=set(), item_scopes=set(),
                  metadata_changes=MetadataChanges([], [], []), discarded_params={"OLD"},
                  skipped_missing_metadata=0, skipped_no_symbols=0, eligible={},
                  items_only=False)
    values.update(overrides)
    return SeedPlan(**values)


def test_ensure_backup_creates_missing_fixed_day_backup_once():
    cursor = RecordingCursor([(0,), (12,)])

    backup, count, created = ensure_backup(RecordingConnection(cursor), date(2026, 10, 28))

    assert (backup, count, created) == ("DF_DIE_TO_WF_YLD_BAK_20261028", 12, True)
    assert any("CREATE TABLE DF_DIE_TO_WF_YLD_BAK_20261028 AS" in sql
               for sql, _ in cursor.calls)


def test_items_come_from_catalog_without_existing_rows():
    catalog = [("P", "PT1H", "o", name, i) for i, name in enumerate(
        ("BVDS", "UNMAPPED", "BVDS(B)", "PT1H", "OLD"), 1)]
    catalog += [("P", "PT1C", "o", "BVDS", 1)]

    assert load_catalog_items({("P", "PT1H", "BVDS(B)")}, catalog, {"OLD"}) == {
                                  ("P", "PT1H", "BVDS"), ("P", "PT1H", "UNMAPPED"),
                                  ("P", "PT1C", "BVDS")}


def test_metadata_uses_any_existing_row_in_supported_scope():
    cursor = RecordingCursor()
    cursor.rows = [("P", "W", 2026, 10, "L", "1", "PT1H TEST", "T"),
                   ("P", "W", 2026, 10, "L", "1", "CFTA TEST", "T")]
    metadata = load_wafer_metadata(RecordingConnection(cursor), {("P", "PT1H")})
    assert set(metadata) == {("P", "L", 1, "PT1H TEST")}


def test_metadata_rejects_conflicting_existing_rows():
    cursor = RecordingCursor()
    cursor.rows = [("P", "W", 2026, 10, "L", "1", "PT1H TEST", "T"),
                   ("P", "W2", 2026, 10, "L", "1", "PT1H TEST", "T")]
    with pytest.raises(ValueError, match="inconsistent wafer metadata"):
        load_wafer_metadata(RecordingConnection(cursor), {("P", "PT1H")})


def test_item_value_is_deterministic_precise_bounded_and_spread():
    import seed_wf_yld_bins as seed

    values = [seed._item_value("P", "L", index, "PT1H TEST", "BVDS")
              for index in range(1000)]

    assert seed._item_value("P", "L", 7, "PT1H TEST", "BVDS") == values[7]
    assert all(Decimal(0) <= value <= Decimal(1) for value in values)
    assert all(value.as_tuple().exponent == -12 for value in values)
    assert len(set(values)) > 950
    assert Decimal("0.005") < sorted(values)[len(values) // 2] < Decimal("0.02")


def test_plan_seed_generates_map_independent_unique_item_rows(monkeypatch):
    import seed_wf_yld_bins as seed

    symbols = [("P", prefix, "BVDS(B)", r"^BB$") for prefix in ("PT1H", "PT1C")]
    symbols += [("P", prefix, "VTH(V)", r"^VV$") for prefix in ("PT1H", "PT1C")]
    catalog = [("P", prefix, "o", name, index)
               for prefix in ("PT1H", "PT1C")
               for index, name in enumerate(("BVDS", "VTH", "UNMAPPED", prefix), 1)]
    metadata = {("P", "L", i, process): Metadata("P", "W", 2026, 10, "L", str(i), process, "T")
                for i, process in ((1, "PT1H TEST"), (2, "PT1C TEST"))}
    maps = [("P", "L", 1, "PT1H TEST", "T", [("B", "?"), ("?", "V"), ("B", "V")]),
            ("P", "L", 2, "PT1C TEST", "T", [("V", "B"), ("?", "?")])]
    monkeypatch.setattr(seed, "load_catalogs", lambda conn: (symbols, catalog))
    monkeypatch.setattr(seed, "load_wafer_metadata", lambda conn, scopes: metadata)
    monkeypatch.setattr(seed, "iter_maps", lambda conn: iter(maps))

    plan = plan_seed(object(), {"OLD"})
    values = {(r[5], r[6]): r[7] for r in plan.rows}
    for wfid, process, prefix in (("1", "PT1H TEST", "PT1H"),
                                  ("2", "PT1C TEST", "PT1C")):
        for name in ("BVDS", "VTH", "UNMAPPED"):
            assert values[(wfid, name)] == seed._item_value("P", "L", wfid, process, name)
    assert ("P", "PT1H TEST", "BVDS") in plan.target_scopes
    assert ("P", "PT1C TEST", "VTH") in plan.target_scopes
    assert all(len(row) == 10 and row[0] == "P" and row[9] == "T" for row in plan.rows)
    assert len(plan.rows) == 12
    assert plan_seed(object(), {"OLD"}).rows == plan.rows

    maps[0] = ("P", "L", 1, "PT1H TEST", "T", [("?", "?"), ("B", "?"), ("?", "V")])
    changed = plan_seed(object(), {"OLD"})
    assert [(row[5], row[6], row[7]) for row in changed.rows if "(" not in row[6]
            and row[6] not in ("PT1H", "PT1C")] == [
                (row[5], row[6], row[7]) for row in plan.rows if "(" not in row[6]
                and row[6] not in ("PT1H", "PT1C")]


def test_plan_seed_keeps_bin_rows_byte_identical_when_items_are_added(monkeypatch):
    import seed_wf_yld_bins as seed

    symbols = [("P", "PT1H", "BVDS(B)", r"^BB$")]
    metadata = {("P", "L", 1, "PT1H TEST"):
                Metadata("P", "W", 2026, 10, "L", "1", "PT1H TEST", "T")}
    maps = [("P", "L", 1, "PT1H TEST", "T", [("B", "?"), ("?", "?")])]
    monkeypatch.setattr(seed, "load_wafer_metadata", lambda conn, scopes: metadata)
    monkeypatch.setattr(seed, "iter_maps", lambda conn: iter(maps))
    monkeypatch.setattr(seed, "load_catalogs", lambda conn: (symbols, [
        ("P", "PT1H", "o", "PT1H", 1)]))
    without_items = plan_seed(object(), {"OLD"})
    monkeypatch.setattr(seed, "load_catalogs", lambda conn: (symbols, [
        ("P", "PT1H", "o", "PT1H", 1), ("P", "PT1H", "o", "BVDS", 2)]))
    with_items = plan_seed(object(), {"OLD"})

    assert [row for row in with_items.rows if row[6] in {"PT1H", "BVDS(B)"}] == without_items.rows


def test_items_only_plan_keeps_only_concrete_item_scopes_and_clears_migration_changes():
    import seed_wf_yld_bins as seed

    bin_row = ("P", "W", 2026, 10, "L", "1", "PT1H", Decimal("0.5"),
               "PT1H TEST", "T")
    item_row = ("P", "W", 2026, 10, "L", "1", "BVDS", Decimal("0.01"),
                "PT1H TEST", "T")
    plan = _empty_plan(
        rows=[bin_row, item_row],
        target_scopes={("P", "PT1H TEST", "PT1H"), ("P", "PT1H TEST", "BVDS")},
        item_scopes={("P", "PT1H", "BVDS")},
        metadata_changes=MetadataChanges([("rename",)], [("insert",)], [("delete",)]),
        eligible={
            ("P", "L", 1, "PT1H TEST"): ([], [], "PT1H"),
            ("P", "OTHER", 2, "PT1C TEST"): ([], [], "PT1C"),
        },
    )

    filtered = seed._items_only_plan(plan)

    assert filtered.rows == [item_row]
    assert filtered.target_scopes == {("P", "PT1H TEST", "BVDS")}
    assert set(filtered.eligible) == {("P", "L", 1, "PT1H TEST")}
    assert filtered.metadata_changes == MetadataChanges([], [], [])
    assert filtered.discarded_params == set()
    assert filtered.items_only is True


def test_generated_counts_rejects_unnormalized_stored_wafer():
    class StoredCursor(RecordingCursor):
        def execute(self, sql, binds=None):
            super().execute(sql, binds)
            if "COUNT(*)" in sql:
                self.rows = [("P", "PT1H TEST", binds["name"], 2)]
            else:
                self.rows = [("P", "L", "1", "PT1H TEST", "PT1H", Decimal("0.5")),
                             ("P", "L", "1", "PT1H TEST", "BVDS(B)", Decimal("0.5")),
                             ("P", "L", "2", "PT1H TEST", "PT1H", Decimal("0.5")),
                             ("P", "L", "2", "PT1H TEST", "BVDS(B)", Decimal("0.4"))]

    rows = [("P", "W", 2026, 10, "L", str(i), param, Decimal("0.5"), "PT1H TEST", "T")
            for i in (1, 2) for param in ("PT1H", "BVDS(B)")]
    patterns = [("BVDS(B)", re.compile("^BB$"))]
    eligible = {("P", "L", i, "PT1H TEST"): ([("?", "?"), ("B", "?")], patterns, "PT1H")
                for i in (1, 2)}
    plan = _empty_plan(rows=rows, target_scopes={("P", "PT1H TEST", "PT1H"),
                                                  ("P", "PT1H TEST", "BVDS(B)")},
                       eligible=eligible)
    with pytest.raises(RuntimeError, match="stored bins-plus-pass sum"):
        _verify_generated_counts(RecordingConnection(StoredCursor()), plan)


def test_sample_rereads_map_and_checks_regenerated_items(monkeypatch):
    import seed_wf_yld_bins as seed

    class StoredCursor(RecordingCursor):
        def __init__(self, wrong=False):
            super().__init__()
            self.rows = [("P", "L", str(i), "PT1H TEST", name, value)
                         for i in range(20)
                         for name, value in (("PT1H", Decimal("0.5")),
                                             ("BVDS(B)", Decimal("0.5")),
                                             ("BVDS", seed._item_value(
                                                 "P", "L", i, "PT1H TEST", "BVDS")
                                              + (Decimal("0.1") if wrong else Decimal(0))),
                                             ("UNMAPPED", seed._item_value(
                                                 "P", "L", i, "PT1H TEST", "UNMAPPED")))]

    patterns = [("BVDS(B)", re.compile("^BB$"))]
    pairs = [("B", "?"), ("?", "?")]
    eligible = {("P", "L", i, "PT1H TEST"): (pairs, patterns, "PT1H")
                for i in range(20)}
    plan = _empty_plan(eligible=eligible,
                       item_scopes={("P", "PT1H", "BVDS"), ("P", "PT1H", "UNMAPPED")},
                       target_scopes={("P", "PT1H TEST", name)
                                      for name in ("PT1H", "BVDS(B)", "BVDS", "UNMAPPED")})
    monkeypatch.setattr(seed, "iter_maps", lambda conn: iter(
        ("P", "L", i, "PT1H TEST", "T", pairs) for i in range(20)))

    assert verify_sample(RecordingConnection(StoredCursor()), plan) == 20
    with pytest.raises(RuntimeError, match="sample verification failed"):
        verify_sample(RecordingConnection(StoredCursor(wrong=True)), plan)


def test_items_only_generated_checks_skip_bin_normalization():
    class StoredCursor(RecordingCursor):
        def execute(self, sql, binds=None):
            super().execute(sql, binds)
            self.rows = [("P", "PT1H TEST", "BVDS", 2)]

    rows = [("P", "W", 2026, 10, "L", str(i), "BVDS", Decimal("0.01"),
             "PT1H TEST", "T") for i in (1, 2)]
    plan = _empty_plan(rows=rows,
                       target_scopes={("P", "PT1H TEST", "BVDS")},
                       item_scopes={("P", "PT1H", "BVDS")},
                       items_only=True)

    _verify_generated_counts(RecordingConnection(StoredCursor()), plan)


def test_replace_rows_scopes_pt1c_only_and_preserves_cfta_and_same_name_other_process():
    cursor = RecordingCursor()
    row = ("P", "W", 2026, 10, "L", "1", "PT1C", Decimal("0.25"),
           "PT1C TEST", "T")
    item_row = ("P", "W", 2026, 10, "L", "1", "BVDS", Decimal("0.25"),
                "PT1C TEST", "T")
    plan = _empty_plan(rows=[row, item_row],
                       target_scopes={("P", "PT1C TEST", "PT1C"),
                                      ("P", "PT1C TEST", "BVDS")},
                       discarded_params=set())

    replace_rows(RecordingConnection(cursor), plan)

    delete_sql, delete_rows = cursor.calls[0]
    assert "LOT_CD=:1 AND PROCESS=:2 AND PARAM=:3" in delete_sql
    assert delete_rows == [("P", "PT1C TEST", "BVDS"), ("P", "PT1C TEST", "PT1C")]
    assert cursor.calls[1][1] == [row, item_row]
    assert all("CFTA" not in str(call) and "PT1H TEST" not in str(call) for call in cursor.calls)


def test_replacement_coverage_aborts_when_existing_wafer_has_no_computed_counterpart():
    cursor = RecordingCursor()
    cursor.rows = [("P", "L", "1", "PT1C TEST", "PT1C")]
    plan = _empty_plan(target_scopes={("P", "PT1C TEST", "PT1C")},
                       discarded_params=set())

    with pytest.raises(RuntimeError, match="lack computed counterparts"):
        verify_replacement_coverage(RecordingConnection(cursor), plan)


def test_unrelated_row_verification_excludes_regenerated_items():
    cursor = RecordingCursor([(0,), (0,), (0,)])
    verify_unrelated_rows(RecordingConnection(cursor), "DF_DIE_TO_WF_YLD_BAK_20261028",
                          {("P", "PT1H TEST", "BVDS")}, set())
    assert all("PARAM=:param_0" in sql for sql, _ in cursor.calls)
    assert all(binds["param_0"] == "BVDS" for _, binds in cursor.calls)


def test_unrelated_row_snapshot_counts_and_hashes_rows_outside_item_scope():
    cursor = RecordingCursor([(12, 3456)])

    assert _unrelated_rows_snapshot(
        RecordingConnection(cursor), {("P", "PT1H TEST", "BVDS")}, set()
    ) == (12, 3456)

    sql, binds = cursor.calls[0]
    assert "COUNT(*)" in sql
    assert "SUM(ORA_HASH(" in sql
    assert "PARAM=:param_0" in sql
    assert binds["param_0"] == "BVDS"


def test_main_rolls_back_when_transaction_step_fails(monkeypatch):
    import common
    import seed_wf_yld_bins as seed

    class Connection:
        def __init__(self):
            self.rollbacks = 0
            self.commits = 0
            self.closed = 0
            self.cursor_value = RecordingCursor()
        def cursor(self):
            return self.cursor_value
        def rollback(self):
            self.rollbacks += 1
        def commit(self):
            self.commits += 1
        def close(self):
            self.closed += 1

    connection = Connection()
    plan = _empty_plan()
    monkeypatch.setattr(seed, "parse_args", lambda: SimpleNamespace(apply=True, items_only=True))
    monkeypatch.setattr(seed, "load_migration", lambda: {"OLD"})
    monkeypatch.setattr(common, "get_oracle_connection", lambda: connection)
    monkeypatch.setattr(seed, "plan_seed", lambda conn, discarded: plan)
    monkeypatch.setattr(seed, "_target_count", lambda conn, scopes: 0)
    monkeypatch.setattr(seed, "ensure_backup", lambda conn, day: (
        "DF_DIE_TO_WF_YLD_BAK_20261028", 1, False))
    monkeypatch.setattr(seed, "verify_replacement_coverage", lambda conn, value: None)
    monkeypatch.setattr(seed, "verify_unrelated_rows", lambda *args: None)
    monkeypatch.setattr(seed, "_unrelated_rows_snapshot", lambda *args: (0, 0))
    monkeypatch.setattr(seed, "update_yield_metadata", lambda conn, value: pytest.fail(
        "items-only must skip metadata updates"))
    monkeypatch.setattr(seed, "replace_rows", lambda conn, value: (_ for _ in ()).throw(
        RuntimeError("write failed")))

    with pytest.raises(RuntimeError, match="write failed"):
        seed.main()

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.closed == 1


def test_main_items_only_preserves_transaction_snapshot_and_commits(monkeypatch):
    import common
    import seed_wf_yld_bins as seed

    events = []

    class Connection:
        def __init__(self):
            self.cursor_value = RecordingCursor()
        def cursor(self):
            return self.cursor_value
        def rollback(self):
            events.append("rollback")
        def commit(self):
            events.append("commit")
        def close(self):
            events.append("close")

    bin_row = ("P", "W", 2026, 10, "L", "1", "PT1H", Decimal("0.5"),
               "PT1H TEST", "T")
    item_row = ("P", "W", 2026, 10, "L", "1", "BVDS", Decimal("0.01"),
                "PT1H TEST", "T")

    def make_plan(conn, discarded):
        return _empty_plan(
            rows=[bin_row, item_row],
            target_scopes={("P", "PT1H TEST", "PT1H"), ("P", "PT1H TEST", "BVDS")},
            item_scopes={("P", "PT1H", "BVDS")},
            metadata_changes=MetadataChanges([("rename",)], [("insert",)], [("delete",)]),
        )

    def target_count(conn, scopes):
        assert scopes == {("P", "PT1H TEST", "BVDS")}
        return 1

    def replace(conn, plan):
        assert plan.rows == [item_row]
        assert plan.discarded_params == set()
        events.append("replace")
        return 1

    monkeypatch.setattr(seed, "parse_args", lambda: SimpleNamespace(apply=True, items_only=True))
    monkeypatch.setattr(seed, "load_migration", lambda: {"OLD"})
    monkeypatch.setattr(common, "get_oracle_connection", lambda: Connection())
    monkeypatch.setattr(seed, "plan_seed", make_plan)
    monkeypatch.setattr(seed, "_target_count", target_count)
    monkeypatch.setattr(seed, "ensure_backup", lambda conn, day: events.append("backup") or (
        "DF_DIE_TO_WF_YLD_BAK_20261028", 10, False))
    monkeypatch.setattr(seed, "verify_replacement_coverage", lambda conn, plan: events.append(
        "coverage"))
    snapshots = iter(((9, 1234), (9, 1234)))
    monkeypatch.setattr(seed, "_unrelated_rows_snapshot",
                        lambda conn, scopes, discarded: events.append("snapshot") or next(snapshots))
    monkeypatch.setattr(seed, "verify_unrelated_rows", lambda *args: pytest.fail(
        "items-only must compare the locked transaction snapshot, not the backup"))
    monkeypatch.setattr(seed, "update_yield_metadata", lambda conn, plan: pytest.fail(
        "items-only must skip metadata updates"))
    monkeypatch.setattr(seed, "replace_rows", replace)
    monkeypatch.setattr(seed, "_verify_generated_counts", lambda conn, plan: events.append("counts"))
    monkeypatch.setattr(seed, "verify_sample", lambda conn, plan: events.append("sample") or 20)

    assert seed.main() == 0
    assert events == ["backup", "coverage", "snapshot", "replace", "snapshot", "counts",
                      "sample", "commit", "close"]


def test_main_commits_only_after_all_fake_migration_checks(monkeypatch):
    import common
    import seed_wf_yld_bins as seed

    events = []

    class Connection:
        def __init__(self):
            self.cursor_value = RecordingCursor()
        def cursor(self):
            return self.cursor_value
        def rollback(self):
            events.append("rollback")
        def commit(self):
            events.append("commit")
        def close(self):
            events.append("close")

    connection = Connection()
    row = ("P", "W", 2026, 10, "L", "1", "PT1C", Decimal("0.25"),
           "PT1C TEST", "T")
    plan = _empty_plan(rows=[row], target_scopes={("P", "PT1C TEST", "PT1C")},
                       discarded_params=set(),
                       eligible={("P", "L", 1, "PT1C TEST"): ([("?", "?")], [], "PT1C")})
    monkeypatch.setattr(seed, "parse_args", lambda: SimpleNamespace(apply=True, items_only=False))
    monkeypatch.setattr(seed, "load_migration", lambda: {"OLD"})
    monkeypatch.setattr(common, "get_oracle_connection", lambda: connection)
    monkeypatch.setattr(seed, "plan_seed", lambda conn, discarded: plan)
    monkeypatch.setattr(seed, "_target_count", lambda conn, scopes: len(plan.rows))
    monkeypatch.setattr(seed, "ensure_backup", lambda conn, day: (
        "DF_DIE_TO_WF_YLD_BAK_20261028", 10, False))
    monkeypatch.setattr(seed, "verify_replacement_coverage",
                        lambda conn, value: events.append("coverage"))
    monkeypatch.setattr(seed, "verify_unrelated_rows", lambda *args: events.append("unrelated"))
    monkeypatch.setattr(seed, "update_yield_metadata",
                        lambda conn, value: events.append("metadata"))
    monkeypatch.setattr(seed, "replace_rows", lambda conn, value: events.append("replace") or 1)
    monkeypatch.setattr(seed, "_verify_generated_counts",
                        lambda conn, value: events.append("counts"))
    monkeypatch.setattr(seed, "verify_sample", lambda conn, value: events.append("sample") or 20)

    assert seed.main() == 0
    assert events == ["coverage", "unrelated", "metadata", "replace", "unrelated", "counts",
                      "sample", "commit", "close"]


def test_parse_map_preserves_bin_whitespace_and_validates_coordinates():
    payload = '{"MAP":["1,2, ,B","3,4,A, "]}'

    assert parse_map(payload) == [(" ", "B"), ("A", " ")]


@pytest.mark.parametrize(
    "payload, message",
    [
        ('{"MAP":[]}', "empty"),
        ('{"MAP":["1,2,A"]}', "four"),
        ('{"MAP":["1,2,AA,B"]}', "single"),
        ('{"MAP":["1,2,A,B","1,2,C,D"]}', "duplicate"),
        ('{"OTHER":[]}', "MAP"),
        ("not-json", "JSON"),
    ],
)
def test_parse_map_rejects_malformed_payloads(payload, message):
    with pytest.raises(ValueError, match=message):
        parse_map(payload)


def test_match_process_returns_unique_prefix_and_reports_missing():
    assert match_process("PT1H TEST", {"PT1H", "PT1C"}) == "PT1H"
    assert match_process("PT1C TEST", {"PT1H", "PT1C"}) == "PT1C"
    assert match_process("OTHER", {"PT1H", "PT1C"}) is None
    assert match_process("PT1H_TEST", {"PT1H"}) is None


def test_match_process_rejects_ambiguous_prefixes():
    with pytest.raises(ValueError, match="ambiguous"):
        match_process("A B C", {"A", "A B"})


def test_compile_patterns_keeps_lots_isolated_and_rejects_conflicts():
    compiled = compile_patterns(
        [
            ("4SA", "PT1H", "DDA(d)", r"^(d.|.d)$"),
            ("4SS", "PT1H", "DDA(d)", r"^dd$"),
        ]
    )
    assert compiled[("4SA", "PT1H")][0][1].fullmatch("dA")
    assert not compiled[("4SS", "PT1H")][0][1].fullmatch("dA")

    with pytest.raises(ValueError, match="conflicting"):
        compile_patterns(
            [("4SA", "PT1H", "P", "^AA$"), ("4SA", "PT1H", "P", "^BB$")]
        )


def test_compile_patterns_rejects_invalid_regex():
    with pytest.raises(ValueError, match="invalid regex"):
        compile_patterns([("4SA", "PT1H", "P", "[")])
