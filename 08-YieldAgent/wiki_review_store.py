from __future__ import annotations

import fcntl
import html
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from models import (
    PluginReview,
    PluginReviewCreate,
    PluginReviewHistory,
    PluginReviewUpdate,
    ReviewStatus,
)
from wiki_config import WikiPaths
from wiki_safe_mutation import FileSnapshot, PinnedWikiMutation


_HISTORY_START = "<!-- yield-wiki:review-history:start -->"
_HISTORY_END = "<!-- yield-wiki:review-history:end -->"
_MARKDOWN_SPECIAL = "\\`*_{}[]()#+-.!|~"


class ReviewNotFound(FileNotFoundError):
    pass


class ReviewConflict(RuntimeError):
    pass


@contextmanager
def _review_lock(paths: WikiPaths):
    with PinnedWikiMutation(paths) as mutation:
        descriptor = mutation.open_lock_file(paths.state_dir / "reviews.lock")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        try:
            yield mutation
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _plain_text(value: object) -> str:
    escaped = " ".join(str(value).split())
    for character in _MARKDOWN_SPECIAL:
        escaped = escaped.replace(character, f"\\{character}")
    return html.escape(escaped, quote=False)


def _render_history(history: list[PluginReviewHistory]) -> str:
    lines = [_HISTORY_START, "## Review History", ""]
    for entry in history:
        lines.extend(
            (
                f"- {_plain_text(entry.changed_at)}: "
                f"{_plain_text(entry.from_status)} → {_plain_text(entry.to_status)}",
                f"  - reviewer: {_plain_text(entry.reviewer)}",
                f"  - comment: {_plain_text(entry.comment)}",
            )
        )
    lines.append(_HISTORY_END)
    return "\n".join(lines)


def _replace_history_block(body: str, history: list[PluginReviewHistory]) -> str:
    block = _render_history(history)
    start = body.find(_HISTORY_START)
    end = body.find(_HISTORY_END, start + len(_HISTORY_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        return body[:start] + block + body[end + len(_HISTORY_END) :]
    separator = "" if body.endswith("\n\n") else "\n" if body.endswith("\n") else "\n\n"
    return body + separator + block + "\n"


def _history_from_metadata(metadata: dict) -> list[PluginReviewHistory]:
    values = metadata.get("history") or []
    return [PluginReviewHistory.model_validate(value) for value in values]


def _review_from_post(post: frontmatter.Post) -> PluginReview:
    metadata = dict(post.metadata)
    history = _history_from_metadata(metadata)
    return PluginReview(
        id=str(metadata["id"]),
        review_type=str(metadata["review_type"]),
        status=metadata["status"],
        target_concept_id=str(metadata["target_concept_id"]),
        version=int(metadata.get("version", 1)),
        created=str(metadata["created"]),
        updated=str(metadata["updated"]),
        body_markdown=post.content or "",
        metadata=metadata,
        history=history,
    )


class WikiReviewStore:
    def __init__(self, paths: WikiPaths):
        self.paths = paths

    def list(self, status: ReviewStatus | None = None) -> list[PluginReview]:
        reviews = []
        with PinnedWikiMutation(self.paths) as mutation:
            for path in mutation.list_paths(self.paths.reviews, suffix=".md"):
                snapshot = mutation.snapshot(path)
                post = _post_from_snapshot(snapshot)
                if post.metadata.get("type") != "review":
                    continue
                review = _review_from_post(post)
                if status is None or review.status == status:
                    reviews.append(review)
        return reviews

    def create(self, request: PluginReviewCreate) -> PluginReview:
        now = _now_iso()
        unique_id = uuid.uuid4().hex
        review_id = f"review:{request.review_type}:{unique_id}"
        path = self.paths.reviews / f"review_{unique_id}.md"
        metadata = {
            "id": review_id,
            "type": "review",
            "review_type": request.review_type,
            "status": "pending",
            "target_concept_id": request.target_concept_id,
            "version": 1,
            "created": now,
            "updated": now,
            "reviewer": request.reviewer,
            "comment": request.comment,
            "history": [],
        }
        title = request.review_type.replace("_", " ").title()
        body = f"# {_plain_text(title)} Review\n\n{_plain_text(request.comment)}\n"
        post = frontmatter.Post(content=body, **metadata)
        with _review_lock(self.paths) as mutation:
            expected = mutation.snapshot(path)
            if expected.exists:
                raise ReviewConflict(review_id)
            mutation.replace_text(
                path,
                frontmatter.dumps(post),
                expected=expected,
            )
        return _review_from_post(post)

    def update(self, review_id: str, request: PluginReviewUpdate) -> PluginReview:
        with _review_lock(self.paths) as mutation:
            path, post, expected = self._find_review(mutation, review_id)
            current_version = int(post.metadata.get("version", 1))
            if current_version != request.expected_version:
                raise ReviewConflict(review_id)

            metadata = dict(post.metadata)
            history = _history_from_metadata(metadata)
            changed_at = _now_iso()
            history.append(
                PluginReviewHistory(
                    changed_at=changed_at,
                    from_status=metadata["status"],
                    to_status=request.status,
                    reviewer=request.reviewer,
                    comment=request.comment,
                )
            )
            metadata["status"] = request.status
            metadata["version"] = current_version + 1
            metadata["updated"] = changed_at
            metadata["history"] = [entry.model_dump() for entry in history]
            updated = frontmatter.Post(
                content=_replace_history_block(post.content or "", history),
                **metadata,
            )
            mutation.replace_text(
                path,
                frontmatter.dumps(updated),
                expected=expected,
            )
            return _review_from_post(updated)

    def _find_review(
        self,
        mutation: PinnedWikiMutation,
        review_id: str,
    ) -> tuple[Path, frontmatter.Post, FileSnapshot]:
        for path in mutation.list_paths(self.paths.reviews, suffix=".md"):
            snapshot = mutation.snapshot(path)
            post = _post_from_snapshot(snapshot)
            if (
                post.metadata.get("type") == "review"
                and post.metadata.get("id") == review_id
            ):
                return path, post, snapshot
        raise ReviewNotFound(review_id)


def _post_from_snapshot(snapshot: FileSnapshot) -> frontmatter.Post:
    return frontmatter.loads(snapshot.content.decode("utf-8"))
