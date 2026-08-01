import frontmatter
import pytest

from wiki_config import initialize_wiki_vault, resolve_wiki_paths
from wiki_plugin_notes import (
    NoteNotFound,
    load_note_context,
    read_source,
    related_notes,
)


pytestmark = pytest.mark.no_server


@pytest.fixture
def paths(tmp_path):
    resolved = resolve_wiki_paths({"WIKI_VAULT_PATH": str(tmp_path / "YieldWiki")})
    initialize_wiki_vault(resolved)
    return resolved


def write_note(path, body, **metadata):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        frontmatter.dumps(frontmatter.Post(content=body, **metadata)), encoding="utf-8"
    )


def write_source(path, doc_id, **metadata):
    write_note(path, "# Source", doc_id=doc_id, type="source", **metadata)


def test_note_context_rejects_vault_escape(paths):
    with pytest.raises(NoteNotFound):
        load_note_context(paths, "../secret.md")


def test_note_context_rejects_symlink_escape(paths, tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    (paths.concepts / "escape.md").symlink_to(outside)

    with pytest.raises(NoteNotFound):
        load_note_context(paths, "concepts/escape.md")


def test_note_context_returns_relative_path_metadata_and_bounded_body(paths):
    write_note(paths.concepts / "A.md", "abcdef", title="A", type="concept")

    result = load_note_context(paths, "concepts/A", max_body_chars=3)

    assert result == {
        "note_path": "concepts/A.md",
        "metadata": {"title": "A", "type": "concept"},
        "body_markdown": "abc",
    }


def test_related_uses_wikilinks_and_backlinks(paths):
    write_note(paths.root / "concepts/A.md", "[[sources/FH-1|FH-1]]")
    write_note(paths.root / "operations/OP.md", "[[concepts/A|A]]")
    write_note(paths.root / "sources/FH-1.md", "source")

    result = related_notes(paths, "concepts/A.md")

    assert [item.path for item in result.outgoing] == ["sources/FH-1.md"]
    assert [item.path for item in result.backlinks] == ["operations/OP.md"]


def test_related_ignores_missing_and_plain_text_targets(paths):
    write_note(
        paths.concepts / "A.md",
        "[[sources/missing|missing]] and sources/FH-1 is plain text",
    )
    write_note(paths.sources / "FH-1.md", "source")

    result = related_notes(paths, "concepts/A.md")

    assert result.outgoing == []


def test_source_returns_only_existing_values(paths):
    write_source(
        paths.sources / "FH-1.md",
        doc_id="FH-1",
        download_url="https://internal/FH-1.pptx",
    )

    result = read_source(paths, "FH-1")

    assert result.source_path == "sources/FH-1.md"
    assert result.download_url == "https://internal/FH-1.pptx"
    assert result.source_file == ""
    assert result.date == ""


def test_source_requires_existing_source_note(paths):
    with pytest.raises(NoteNotFound):
        read_source(paths, "FH-404")


def test_source_rejects_traversal_shaped_identifier(paths):
    write_source(paths.sources / "_FH-1.md", doc_id="../FH-1")

    with pytest.raises(NoteNotFound):
        read_source(paths, "../FH-1")
