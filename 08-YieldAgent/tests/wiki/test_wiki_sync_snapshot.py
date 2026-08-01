from copy import deepcopy

import pytest

from wiki_sync import (
    build_triple_snapshot,
    classify_snapshot,
    find_removed_triples,
    make_triple_key,
    normalize_fail_type,
)


pytestmark = pytest.mark.no_server


def _doc(doc_id: str = "FH-1", **overrides):
    document = {
        "doc_id": doc_id,
        "content": "failure description",
        "cause": "oxide damage",
        "action": "clean chamber",
        "comment": "confirmed",
        "date": "2026-07-31",
        "source_file": "weekly-report.pptx",
        "product": "4SS",
        "fail_type": "EASY(W)",
        "cause_oper": "PRE METAL CLN",
        "embedding": [0.1, 0.2],
    }
    document.update(overrides)
    return document


def test_normalizes_only_the_existing_fail_type_suffix_contract():
    assert normalize_fail_type("EASY(W)") == "EASY"
    assert normalize_fail_type("IDSAT(I)") == "IDSAT"
    assert normalize_fail_type("GATE_OX(G)") == "GATE_OX"
    assert normalize_fail_type("JUNCTION(J)") == "JUNCTION"
    assert normalize_fail_type("UNLISTED") == "UNLISTED"


def test_triple_key_preserves_exact_product_and_operation():
    key = make_triple_key("4SS ", "EASY(W)", "PRE METAL CLN ")

    assert key.product == "4SS "
    assert key.fail_type == "EASY"
    assert key.cause_oper == "PRE METAL CLN "
    assert key.canonical == "4SS |PRE METAL CLN |EASY"


def test_snapshot_fingerprint_is_order_independent_and_embedding_independent():
    key = make_triple_key("4SS", "EASY(W)", "PRE METAL CLN")
    first = _doc("FH-1")
    second = _doc("FH-2", content="another source")

    baseline = build_triple_snapshot(key, [first, second])
    changed_embedding = deepcopy(first)
    changed_embedding["embedding"] = [999.0]
    reordered = build_triple_snapshot(key, [second, changed_embedding])

    assert baseline.source_fingerprint == reordered.source_fingerprint
    assert baseline.source_doc_ids == ("FH-1", "FH-2")
    assert baseline.evidence_count == 2
    assert baseline.evidence_scope == "multiple_sources"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("content", "changed content"),
        ("cause", "changed cause"),
        ("action", "changed action"),
        ("comment", "changed comment"),
        ("date", "2026-08-01"),
        ("source_file", "changed.pptx"),
        ("product", "5AA"),
        ("fail_type", "EASY"),
        ("cause_oper", "METAL CLN"),
    ],
)
def test_every_semantic_source_field_changes_the_fingerprint(field, value):
    key = make_triple_key("4SS", "EASY(W)", "PRE METAL CLN")

    baseline = build_triple_snapshot(key, [_doc()])
    changed = build_triple_snapshot(key, [_doc(**{field: value})])

    assert baseline.source_fingerprint != changed.source_fingerprint


def test_change_classification_distinguishes_addition_and_removal():
    key = make_triple_key("4SS", "EASY(W)", "PRE METAL CLN")
    one = build_triple_snapshot(key, [_doc("FH-1")])
    two = build_triple_snapshot(key, [_doc("FH-1"), _doc("FH-2")])
    previous_one = {
        "source_fingerprint": one.source_fingerprint,
        "source_doc_ids": list(one.source_doc_ids),
    }
    previous_two = {
        "source_fingerprint": two.source_fingerprint,
        "source_doc_ids": list(two.source_doc_ids),
    }

    assert classify_snapshot(one, None) == "new"
    assert classify_snapshot(one, previous_one) == "unchanged"
    assert classify_snapshot(two, previous_one) == "changed"
    assert classify_snapshot(one, previous_two) == "source_removed"
    assert one.evidence_count == 1
    assert one.evidence_scope == "single_source"


def test_find_removed_triples_includes_triples_missing_entirely():
    key = make_triple_key("4SS", "EASY(W)", "PRE METAL CLN")
    snapshot = build_triple_snapshot(key, [_doc()])
    manifest = {
        "triples": {
            key.canonical: {
                "source_fingerprint": snapshot.source_fingerprint,
                "source_doc_ids": ["FH-1"],
            },
            "5AA|ETCH|IDSAT": {
                "source_fingerprint": "sha256:old",
                "source_doc_ids": ["FH-9"],
            },
        }
    }

    assert find_removed_triples({key.canonical: snapshot}, manifest) == [
        "5AA|ETCH|IDSAT"
    ]
