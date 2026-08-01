from copy import deepcopy

import pytest

from wiki_manifest import empty_manifest, record_success
from wiki_sync import (
    OpenSearchWikiScanner,
    build_triple_snapshot,
    make_triple_key,
    plan_sync,
)


pytestmark = pytest.mark.no_server


SOURCE_FIELDS = {
    "doc_id",
    "content",
    "cause",
    "action",
    "comment",
    "date",
    "source_file",
    "product",
    "fail_type",
    "cause_oper",
}


def _doc(doc_id, product, fail_type, cause_oper, content="source"):
    return {
        "doc_id": doc_id,
        "content": content,
        "cause": "cause",
        "action": "action",
        "comment": "comment",
        "date": "2026-08-01",
        "source_file": f"{doc_id}.pptx",
        "product": product,
        "fail_type": fail_type,
        "cause_oper": cause_oper,
        "embedding": [1.0],
    }


class FakeOpenSearch:
    def __init__(self, pages, documents, failure=None):
        self.pages = list(pages)
        self.documents = documents
        self.failure = failure
        self.calls = []

    def search(self, *, index, body):
        self.calls.append((index, deepcopy(body)))
        if self.failure:
            raise self.failure
        if body.get("size") == 0:
            return self.pages.pop(0)
        filters = body["query"]["bool"]["filter"]
        values = {}
        for entry in filters:
            field, value = next(iter(entry["term"].items()))
            values[field] = value
        key = (
            values["product.keyword"],
            values["fail_type.keyword"],
            values["cause_oper"],
        )
        return {
            "hits": {
                "hits": [
                    {"_source": deepcopy(document)}
                    for document in self.documents.get(key, [])
                ]
            }
        }


def _page(buckets, after_key=None):
    aggregation = {"buckets": buckets}
    if after_key is not None:
        aggregation["after_key"] = after_key
    return {"aggregations": {"triples": aggregation}}


def _bucket(product, fail_type, cause_oper, count=1):
    return {
        "key": {
            "product": product,
            "fail_type": fail_type,
            "cause_oper": cause_oper,
        },
        "doc_count": count,
    }


def test_scan_follows_every_composite_after_key_and_fetches_exact_sources():
    pages = [
        _page(
            [_bucket("4SS", "EASY(W)", "PRE METAL CLN")],
            after_key={
                "product": "4SS",
                "fail_type": "EASY(W)",
                "cause_oper": "PRE METAL CLN",
            },
        ),
        _page([_bucket("5AA", "IDSAT(I)", "ETCH")]),
    ]
    documents = {
        ("4SS", "EASY(W)", "PRE METAL CLN"): [
            _doc("FH-1", "4SS", "EASY(W)", "PRE METAL CLN")
        ],
        ("5AA", "IDSAT(I)", "ETCH"): [
            _doc("FH-2", "5AA", "IDSAT(I)", "ETCH")
        ],
    }
    client = FakeOpenSearch(pages, documents)

    snapshots = OpenSearchWikiScanner(client, "fail-history").scan()

    assert sorted(snapshots) == ["4SS|PRE METAL CLN|EASY", "5AA|ETCH|IDSAT"]
    aggregation_calls = [body for _, body in client.calls if body.get("size") == 0]
    assert len(aggregation_calls) == 2
    assert "after" not in aggregation_calls[0]["aggs"]["triples"]["composite"]
    assert aggregation_calls[1]["aggs"]["triples"]["composite"]["after"] == {
        "product": "4SS",
        "fail_type": "EASY(W)",
        "cause_oper": "PRE METAL CLN",
    }
    fetch_calls = [body for _, body in client.calls if body.get("size") != 0]
    assert len(fetch_calls) == 2
    assert set(fetch_calls[0]["_source"]) == SOURCE_FIELDS
    assert "embedding" not in fetch_calls[0]["_source"]
    assert {next(iter(item["term"])) for item in fetch_calls[0]["query"]["bool"]["filter"]} == {
        "product.keyword",
        "fail_type.keyword",
        "cause_oper",
    }


def test_scan_merges_raw_fail_types_that_share_a_canonical_key():
    pages = [
        _page(
            [
                _bucket("4SS", "EASY", "PRE METAL CLN"),
                _bucket("4SS", "EASY(W)", "PRE METAL CLN"),
            ]
        )
    ]
    documents = {
        ("4SS", "EASY", "PRE METAL CLN"): [
            _doc("FH-1", "4SS", "EASY", "PRE METAL CLN")
        ],
        ("4SS", "EASY(W)", "PRE METAL CLN"): [
            _doc("FH-2", "4SS", "EASY(W)", "PRE METAL CLN")
        ],
    }

    snapshots = OpenSearchWikiScanner(
        FakeOpenSearch(pages, documents), "fail-history"
    ).scan()

    snapshot = snapshots["4SS|PRE METAL CLN|EASY"]
    assert snapshot.source_doc_ids == ("FH-1", "FH-2")
    assert snapshot.raw_fail_types == ("EASY", "EASY(W)")
    assert snapshot.evidence_count == 2


def test_scan_failure_propagates_without_returning_a_partial_plan():
    scanner = OpenSearchWikiScanner(
        FakeOpenSearch([], {}, failure=RuntimeError("OpenSearch unavailable")),
        "fail-history",
    )

    with pytest.raises(RuntimeError, match="OpenSearch unavailable"):
        scanner.scan()


def test_plan_reports_every_change_class_and_missing_document_ids():
    unchanged = build_triple_snapshot(
        make_triple_key("UNCHANGED", "EASY", "OP"),
        [_doc("FH-U", "UNCHANGED", "EASY", "OP")],
    )
    changed_old = build_triple_snapshot(
        make_triple_key("CHANGED", "EASY", "OP"),
        [_doc("FH-C", "CHANGED", "EASY", "OP", content="old")],
    )
    changed_now = build_triple_snapshot(
        changed_old.key,
        [_doc("FH-C", "CHANGED", "EASY", "OP", content="new")],
    )
    removed_old = build_triple_snapshot(
        make_triple_key("REMOVED", "EASY", "OP"),
        [
            _doc("FH-R1", "REMOVED", "EASY", "OP"),
            _doc("FH-R2", "REMOVED", "EASY", "OP"),
        ],
    )
    removed_now = build_triple_snapshot(
        removed_old.key,
        [_doc("FH-R1", "REMOVED", "EASY", "OP")],
    )
    gone = build_triple_snapshot(
        make_triple_key("GONE", "EASY", "OP"),
        [_doc("FH-G", "GONE", "EASY", "OP")],
    )
    new = build_triple_snapshot(
        make_triple_key("NEW", "EASY", "OP"),
        [_doc("FH-N", "NEW", "EASY", "OP")],
    )
    manifest = empty_manifest("fail-history")
    for snapshot in (unchanged, changed_old, removed_old, gone):
        record_success(
            manifest,
            snapshot,
            concept_id=f"concept:{snapshot.key.canonical}",
            concept_version=1,
            success_at="2026-08-01T00:00:00+00:00",
        )

    plan = plan_sync(
        {
            unchanged.key.canonical: unchanged,
            changed_now.key.canonical: changed_now,
            removed_now.key.canonical: removed_now,
            new.key.canonical: new,
        },
        manifest,
    )

    assert [change.triple_key for change in plan.unchanged] == [unchanged.key.canonical]
    assert [change.triple_key for change in plan.changed] == [changed_old.key.canonical]
    assert [change.triple_key for change in plan.new] == [new.key.canonical]
    assert [change.triple_key for change in plan.source_removed] == [
        gone.key.canonical,
        removed_old.key.canonical,
    ]
    missing = {change.triple_key: change.missing_doc_ids for change in plan.source_removed}
    assert missing[gone.key.canonical] == ("FH-G",)
    assert missing[removed_old.key.canonical] == ("FH-R2",)
