from decimal import Decimal
from datetime import date
import re
from types import SimpleNamespace

import pytest

from seed_wf_yld_bins import (
    _metadata_changes,
    MetadataChanges,
    SeedPlan,
    _baseline_table,
    compile_patterns,
    compute_ratios,
    ensure_backup,
    match_process,
    parse_map,
    replace_rows,
    verify_replacement_coverage,
)


pytestmark = pytest.mark.no_server


def test_compute_ratios_counts_pass_pair_and_full_string_matches():
    pairs = [("?", "B"), ("B", "?"), ("B", "B"), ("?", "?")]
    patterns = [("B_EITHER", re.compile(r"^(B.|.B)$"))]

    result = compute_ratios(pairs, patterns, "?", "PT1H")

    assert result == {
        "PT1H": Decimal("0.250000000000"),
        "B_EITHER": Decimal("0.750000000000"),
    }


def test_compute_ratios_emits_zero_and_allows_overlapping_patterns():
    pairs = [("A", "B"), ("A", "A")]
    patterns = [
        ("LEFT_A", re.compile(r"^A.$")),
        ("ANY_A", re.compile(r"^.*A.*$")),
        ("NEVER", re.compile(r"^ZZ$")),
    ]

    result = compute_ratios(pairs, patterns, "?", "PT1C")

    assert result == {
        "PT1C": Decimal("0E-12"),
        "LEFT_A": Decimal("1.000000000000"),
        "ANY_A": Decimal("1.000000000000"),
        "NEVER": Decimal("0E-12"),
    }


def test_compute_ratios_counts_double_side_match_once_and_rounds_half_even():
    pairs = [("A", "A"), ("B", "B"), ("C", "C")]
    patterns = [("A", re.compile(r"^(A.|.A)$"))]

    result = compute_ratios(pairs, patterns, "?", "PT1H")

    assert result["A"] == Decimal("0.333333333333")


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
                  skipped_missing_item=0, skipped_no_symbols=0, eligible={})
    values.update(overrides)
    return SeedPlan(**values)


def test_ensure_backup_creates_missing_fixed_day_backup_once():
    cursor = RecordingCursor([(0,), (12,)])

    backup, count, created = ensure_backup(RecordingConnection(cursor), date(2026, 10, 28))

    assert (backup, count, created) == ("DF_DIE_TO_WF_YLD_BAK_20261028", 12, True)
    assert any("CREATE TABLE DF_DIE_TO_WF_YLD_BAK_20261028 AS" in sql
               for sql, _ in cursor.calls)


def test_baseline_uses_only_fixed_migration_backup_when_present():
    cursor = RecordingCursor()
    cursor.rows = [("DF_DIE_TO_WF_YLD_BAK_20260101",),
                   ("DF_DIE_TO_WF_YLD_BAK_20261028",)]

    assert _baseline_table(RecordingConnection(cursor)) == "DF_DIE_TO_WF_YLD_BAK_20261028"


def test_replace_rows_scopes_pt1c_only_and_preserves_cfta_and_same_name_other_process():
    cursor = RecordingCursor()
    row = ("P", "W", 2026, 10, "L", "1", "PT1C", Decimal("0.25"),
           "PT1C TEST", "T")
    plan = _empty_plan(rows=[row], target_scopes={("P", "PT1C TEST", "PT1C")},
                       discarded_params=set())

    replace_rows(RecordingConnection(cursor), plan)

    delete_sql, delete_rows = cursor.calls[0]
    assert "LOT_CD=:1 AND PROCESS=:2 AND PARAM=:3" in delete_sql
    assert delete_rows == [("P", "PT1C TEST", "PT1C")]
    assert all("CFTA" not in str(call) and "PT1H TEST" not in str(call) for call in cursor.calls)


def test_replacement_coverage_aborts_when_existing_wafer_has_no_computed_counterpart():
    cursor = RecordingCursor()
    cursor.rows = [("P", "L", "1", "PT1C TEST", "PT1C")]
    plan = _empty_plan(target_scopes={("P", "PT1C TEST", "PT1C")},
                       discarded_params=set())

    with pytest.raises(RuntimeError, match="lack computed counterparts"):
        verify_replacement_coverage(RecordingConnection(cursor), plan)


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
    monkeypatch.setattr(seed, "parse_args", lambda: SimpleNamespace(apply=True))
    monkeypatch.setattr(seed, "load_migration", lambda: {"OLD"})
    monkeypatch.setattr(common, "get_oracle_connection", lambda: connection)
    monkeypatch.setattr(seed, "plan_seed", lambda conn, discarded: plan)
    monkeypatch.setattr(seed, "_target_count", lambda conn, scopes: 0)
    monkeypatch.setattr(seed, "ensure_backup", lambda conn, day: (
        "DF_DIE_TO_WF_YLD_BAK_20261028", 1, False))
    monkeypatch.setattr(seed, "verify_replacement_coverage", lambda conn, value: None)
    monkeypatch.setattr(seed, "verify_items", lambda *args: None)
    monkeypatch.setattr(seed, "update_yield_metadata", lambda conn, value: None)
    monkeypatch.setattr(seed, "replace_rows", lambda conn, value: (_ for _ in ()).throw(
        RuntimeError("write failed")))

    with pytest.raises(RuntimeError, match="write failed"):
        seed.main()

    assert connection.rollbacks == 1
    assert connection.commits == 0
    assert connection.closed == 1


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
                       eligible={("P", "L", 1, "PT1C TEST"): ([("?", "?")], [])})
    monkeypatch.setattr(seed, "parse_args", lambda: SimpleNamespace(apply=True))
    monkeypatch.setattr(seed, "load_migration", lambda: {"OLD"})
    monkeypatch.setattr(common, "get_oracle_connection", lambda: connection)
    monkeypatch.setattr(seed, "plan_seed", lambda conn, discarded: plan)
    monkeypatch.setattr(seed, "_target_count", lambda conn, scopes: len(plan.rows))
    monkeypatch.setattr(seed, "ensure_backup", lambda conn, day: (
        "DF_DIE_TO_WF_YLD_BAK_20261028", 10, False))
    monkeypatch.setattr(seed, "verify_replacement_coverage",
                        lambda conn, value: events.append("coverage"))
    monkeypatch.setattr(seed, "verify_items", lambda *args: events.append("items"))
    monkeypatch.setattr(seed, "update_yield_metadata",
                        lambda conn, value: events.append("metadata"))
    monkeypatch.setattr(seed, "replace_rows", lambda conn, value: events.append("replace") or 1)
    monkeypatch.setattr(seed, "_verify_generated_counts",
                        lambda conn, value: events.append("counts"))
    monkeypatch.setattr(seed, "verify_sample", lambda conn, value: events.append("sample") or 20)

    assert seed.main() == 0
    assert events == ["coverage", "items", "metadata", "replace", "items", "counts",
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
