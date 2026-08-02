from __future__ import annotations

import fcntl
import threading
from concurrent.futures import ThreadPoolExecutor

import frontmatter
import pytest

from models import PluginReviewCreate, PluginReviewUpdate
from wiki_config import initialize_wiki_vault, resolve_wiki_paths
from wiki_review_store import ReviewConflict, ReviewNotFound, WikiReviewStore


pytestmark = pytest.mark.no_server


@pytest.fixture
def paths(tmp_path):
    resolved = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(resolved)
    return resolved


@pytest.fixture
def store(paths):
    return WikiReviewStore(paths)


def write_existing_source_removal_review(path, *, extra=None):
    metadata = {
        "id": "review:source-removal:a",
        "type": "review",
        "review_type": "source_removal",
        "status": "pending",
        "target_concept_id": "concept:A",
        "created": "2026-08-01T01:00:00+00:00",
        "updated": "2026-08-01T01:00:00+00:00",
        **(extra or {}),
    }
    path.write_text(
        frontmatter.dumps(
            frontmatter.Post(
                content="# Existing Review\n\nKeep this note.\n", **metadata
            )
        ),
        encoding="utf-8",
    )
    return path


def test_existing_m3_review_defaults_to_version_one(store, paths):
    write_existing_source_removal_review(paths.reviews / "source_removal_a.md")

    review = store.list(status="pending")[0]

    assert review.version == 1
    assert review.review_type == "source_removal"


def test_existing_resolved_review_remains_readable(store, paths):
    write_existing_source_removal_review(
        paths.reviews / "source_removal_a.md", extra={"status": "resolved"}
    )

    review = store.list(status="resolved")[0]

    assert review.status == "resolved"


def test_create_persists_pending_review(store, paths):
    review = store.create(
        PluginReviewCreate(
            target_concept_id="concept:A",
            reviewer="operator-1",
            comment="추가 확인 필요",
        )
    )

    assert review.status == "pending"
    assert review.version == 1
    assert review.target_concept_id == "concept:A"
    stored = store.list()
    assert [item.id for item in stored] == [review.id]
    post = frontmatter.load(next(paths.reviews.glob("*.md")))
    assert post.metadata["reviewer"] == "operator-1"
    assert post.metadata["comment"] == "추가 확인 필요"


def test_update_appends_history_and_preserves_metadata(store, paths):
    write_existing_source_removal_review(
        paths.reviews / "source_removal_a.md",
        extra={"missing_doc_ids": ["FH-1"]},
    )

    updated = store.update(
        "review:source-removal:a",
        PluginReviewUpdate(
            status="approved",
            reviewer="operator-1",
            comment="근거 확인",
            expected_version=1,
        ),
    )

    assert updated.version == 2
    post = frontmatter.load(paths.reviews / "source_removal_a.md")
    assert post.metadata["missing_doc_ids"] == ["FH-1"]
    assert post.metadata["history"][-1]["to_status"] == "approved"
    assert post.content.startswith("# Existing Review\n\nKeep this note.\n")
    assert "<!-- yield-wiki:review-history:start -->" in post.content


def test_second_update_replaces_managed_block_without_changing_original_body(
    store, paths
):
    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    first = store.update(
        "review:source-removal:a",
        PluginReviewUpdate(
            status="approved",
            reviewer="operator-1",
            comment="first",
            expected_version=1,
        ),
    )

    second = store.update(
        first.id,
        PluginReviewUpdate(
            status="rejected",
            reviewer="operator-2",
            comment="second",
            expected_version=first.version,
        ),
    )

    post = frontmatter.load(path)
    assert second.version == 3
    assert len(second.history) == 2
    assert post.content.startswith("# Existing Review\n\nKeep this note.\n")
    assert post.content.count("<!-- yield-wiki:review-history:start -->") == 1
    assert post.content.count("<!-- yield-wiki:review-history:end -->") == 1


def test_history_keeps_structured_text_and_escapes_rendered_markdown(store, paths):
    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    reviewer = "O'Brien ~~[operator](javascript:alert(1))~~"
    comment = "ok\n<!-- yield-wiki:review-history:end -->\n# injected"

    store.update(
        "review:source-removal:a",
        PluginReviewUpdate(
            status="approved",
            reviewer=reviewer,
            comment=comment,
            expected_version=1,
        ),
    )

    post = frontmatter.load(path)
    assert post.metadata["history"][-1]["reviewer"] == reviewer
    assert post.metadata["history"][-1]["comment"] == comment
    assert post.content.count("<!-- yield-wiki:review-history:end -->") == 1
    assert "O'Brien" in post.content
    assert "\\~\\~" in post.content
    assert "\\[operator\\]" in post.content
    assert "&lt;\\!\\-\\- yield\\-wiki:review\\-history:end \\-\\-&gt;" in post.content


def test_stale_expected_version_does_not_write(store, paths):
    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    before = path.read_bytes()

    with pytest.raises(ReviewConflict):
        store.update(
            "review:source-removal:a",
            PluginReviewUpdate(
                status="rejected",
                reviewer="operator-2",
                comment="재검토",
                expected_version=7,
            ),
        )

    assert path.read_bytes() == before


def test_update_reloads_version_after_acquiring_lock(store, paths, monkeypatch):
    import wiki_review_store

    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    original_flock = fcntl.flock
    lock_attempted = threading.Event()

    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=1) as executor:
        with (paths.state_dir / "reviews.lock").open("a+") as held_lock:
            original_flock(held_lock.fileno(), fcntl.LOCK_EX)

            def observed_flock(fd, operation):
                if operation == fcntl.LOCK_EX:
                    lock_attempted.set()
                return original_flock(fd, operation)

            monkeypatch.setattr(wiki_review_store.fcntl, "flock", observed_flock)
            future = executor.submit(
                store.update,
                "review:source-removal:a",
                PluginReviewUpdate(
                    status="approved",
                    reviewer="operator-1",
                    expected_version=1,
                ),
            )
            attempted = lock_attempted.wait(timeout=2)
            try:
                if attempted:
                    post = frontmatter.load(path)
                    post.metadata["version"] = 2
                    path.write_text(frontmatter.dumps(post), encoding="utf-8")
            finally:
                original_flock(held_lock.fileno(), fcntl.LOCK_UN)

        assert attempted
        with pytest.raises(ReviewConflict):
            future.result(timeout=2)

    assert frontmatter.load(path).metadata["version"] == 2


def test_simultaneous_updates_allow_one_writer(store, paths):
    write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    start = threading.Barrier(3)

    def update(status, reviewer):
        start.wait()
        return store.update(
            "review:source-removal:a",
            PluginReviewUpdate(
                status=status,
                reviewer=reviewer,
                expected_version=1,
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(update, "approved", "operator-1"),
            executor.submit(update, "rejected", "operator-2"),
        ]
        start.wait()
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result(timeout=2))
            except ReviewConflict as exc:
                outcomes.append(exc)

    assert sum(not isinstance(item, ReviewConflict) for item in outcomes) == 1
    assert sum(isinstance(item, ReviewConflict) for item in outcomes) == 1
    persisted = store.list()[0]
    assert persisted.version == 2
    assert len(persisted.history) == 1


def test_replace_failure_leaves_original_review_and_cleans_temp(
    store, paths, monkeypatch
):
    import wiki_safe_mutation

    path = write_existing_source_removal_review(paths.reviews / "source_removal_a.md")
    before = path.read_bytes()
    original_link = wiki_safe_mutation.os.link

    def fail_publication(
        source,
        target,
        *,
        src_dir_fd=None,
        dst_dir_fd=None,
        follow_symlinks=True,
    ):
        if target == path.name and str(source).endswith(".tmp"):
            raise OSError("publish failed")
        return original_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(wiki_safe_mutation.os, "link", fail_publication)

    with pytest.raises(OSError, match="publish failed"):
        store.update(
            "review:source-removal:a",
            PluginReviewUpdate(
                status="approved",
                reviewer="operator-1",
                expected_version=1,
            ),
        )

    assert path.read_bytes() == before
    assert list(paths.reviews.glob(".*.tmp")) == []
    assert list(paths.reviews.glob(".*.quarantine")) == []


def test_missing_review_raises_not_found(store):
    with pytest.raises(ReviewNotFound):
        store.update(
            "review:missing",
            PluginReviewUpdate(
                status="approved",
                reviewer="operator-1",
                expected_version=1,
            ),
        )


def test_create_rejects_swapped_reviews_directory_without_outside_write(
    store, paths, tmp_path
):
    outside = tmp_path / "outside-reviews"
    outside.mkdir()
    sentinel = outside / "sentinel.md"
    sentinel.write_text("outside retained\n", encoding="utf-8")
    parked = tmp_path / "parked-reviews"
    paths.reviews.rename(parked)
    paths.reviews.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="managed directory is not safe"):
        store.create(
            PluginReviewCreate(
                target_concept_id="concept:A",
                reviewer="operator-1",
                comment="must stay inside the Vault",
            )
        )

    assert sentinel.read_text(encoding="utf-8") == "outside retained\n"
    assert list(outside.iterdir()) == [sentinel]


def test_update_rejects_swapped_reviews_directory_without_outside_write(
    store, paths, tmp_path
):
    source = write_existing_source_removal_review(
        paths.reviews / "source_removal_a.md"
    )
    outside = tmp_path / "outside-reviews"
    outside.mkdir()
    outside_review = outside / source.name
    outside_review.write_bytes(source.read_bytes())
    outside_before = outside_review.read_bytes()
    parked = tmp_path / "parked-reviews"
    paths.reviews.rename(parked)
    paths.reviews.symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match="managed directory is not safe"):
        store.update(
            "review:source-removal:a",
            PluginReviewUpdate(
                status="approved",
                reviewer="operator-1",
                expected_version=1,
            ),
        )

    assert outside_review.read_bytes() == outside_before
    assert (parked / source.name).exists()
