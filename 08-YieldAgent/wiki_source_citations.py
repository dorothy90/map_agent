"""Markdown-aware extraction of standalone canonical Wiki Source citations."""

from __future__ import annotations

import re
import string

from markdown_it import MarkdownIt

_SOURCE_DOC_ID_RE = re.compile(
    r"FH[-:][A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)*"
)
_BRACKETED_SOURCE_RE = re.compile(
    rf"\[(?P<doc_id>{_SOURCE_DOC_ID_RE.pattern})\]"
)
_MARKDOWN = MarkdownIt()
_COMMONMARK_ESCAPABLE_PUNCTUATION = frozenset(string.punctuation) - {"\\"}


def _mask_escaped_punctuation(markdown: str) -> str:
    sentinel = "\ue000"
    while sentinel in markdown:
        sentinel += "\ue001"

    masked = list(markdown)
    for index, character in enumerate(markdown):
        if character not in _COMMONMARK_ESCAPABLE_PUNCTUATION:
            continue
        backslash_count = 0
        position = index - 1
        while position >= 0 and markdown[position] == "\\":
            backslash_count += 1
            position -= 1
        if backslash_count % 2 == 1:
            masked[index] = sentinel
    return "".join(masked)


def _extract_from_text(text: str) -> set[str]:
    cited_ids: set[str] = set()
    cursor = 0

    while True:
        start = text.find("[", cursor)
        if start == -1:
            break
        end = text.find("]", start + 1)
        if end == -1:
            break

        token = text[start + 1 : end]
        suffix = end + 1
        definition = suffix
        while definition < len(text) and text[definition] in " \t":
            definition += 1

        is_nested_bracket = (
            (start > 0 and text[start - 1] in "[]")
            or (start + 1 < len(text) and text[start + 1] == "[")
            or (end + 1 < len(text) and text[end + 1] in "[]")
        )
        is_markdown_link = suffix < len(text) and text[suffix] in "(["
        is_reference_definition = (
            definition < len(text) and text[definition] == ":"
        )

        if (
            not (start > 0 and text[start - 1] == "!")
            and not is_nested_bracket
            and not is_markdown_link
            and not is_reference_definition
            and _SOURCE_DOC_ID_RE.fullmatch(token)
        ):
            cited_ids.add(token)

        cursor = end + 1

    return cited_ids


def extract_standalone_source_ids(markdown: str) -> set[str]:
    """Return canonical Source IDs cited in ordinary standalone Markdown text."""
    cited_ids: set[str] = set()

    for block in _MARKDOWN.parse(_mask_escaped_punctuation(str(markdown or ""))):
        if block.type != "inline":
            continue
        link_depth = 0
        for child in block.children or []:
            if child.type == "link_open":
                link_depth += 1
            elif child.type == "link_close":
                link_depth = max(0, link_depth - 1)
            elif child.type == "text" and link_depth == 0:
                cited_ids.update(_extract_from_text(child.content))

    return cited_ids


def remove_invalid_standalone_source_citations(
    markdown: str, valid_source_ids: set[str]
) -> str:
    """Remove invalid standalone Source markers while preserving other Markdown."""

    text = str(markdown or "")
    removals: list[tuple[int, int]] = []
    for index, match in enumerate(_BRACKETED_SOURCE_RE.finditer(text)):
        doc_id = match.group("doc_id")
        if doc_id in valid_source_ids:
            continue
        probe_id = f"FH-CITATION-PROBE-{index}"
        while probe_id in text:
            probe_id += "X"
        probe = text[: match.start("doc_id")] + probe_id + text[match.end("doc_id") :]
        if probe_id in extract_standalone_source_ids(probe):
            removals.append(match.span())

    for start, end in reversed(removals):
        text = text[:start] + text[end:]
    return text
