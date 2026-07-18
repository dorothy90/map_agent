from __future__ import annotations

import io
import base64
import zipfile
from pathlib import Path

import pytest

from artifact_context import artifact_scope
from artifact_store import ArtifactRef, ArtifactStore


PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _store(tmp_path: Path, job_id: str = "ppt-job") -> ArtifactStore:
    return ArtifactStore(tmp_path, owner_hash="owner", job_id=job_id)


def _artifact(ref: ArtifactRef) -> dict:
    return {
        "type": ref.artifact_type,
        "mime": ref.mime,
        "title": ref.title,
        "agent": ref.agent,
        "artifact_ref": ref.model_dump(),
    }


def test_ppt_resolver_reads_validated_refs_through_store(tmp_path, monkeypatch):
    import ppt_export_agent as agent

    store = _store(tmp_path)
    ref = store.write_text(
        "<h1>WADS</h1>", mime="text/html", title="wads", agent="wads_agent"
    )
    opened = []
    real_open = store.open

    def recording_open(validated_ref):
        opened.append(validated_ref)
        return real_open(validated_ref)

    monkeypatch.setattr(store, "open", recording_open)
    resolved = agent._resolve_artifact_data(store, [_artifact(ref)])

    assert resolved[0]["data"] == "<h1>WADS</h1>"
    assert opened == [ref]


def test_referenced_image_still_renders_into_pptx(tmp_path):
    import ppt_builder
    import ppt_export_agent as agent

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )
    store = _store(tmp_path)
    ref = store.write_bytes(
        png, mime="image/png", title="wafer-map", agent="map_agent", artifact_type="image"
    )
    resolved = agent._resolve_artifact_data(store, [_artifact(ref)])
    pptx_bytes = ppt_builder.YieldReportPPTBuilder().build(
        {"lotcd": "4SS", "ref_date": "20260718", "map_artifacts": resolved}
    )

    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as archive:
        assert any(name.startswith("ppt/media/image") for name in archive.namelist())


def test_referenced_html_still_renders_into_pptx(tmp_path):
    import ppt_builder
    import ppt_export_agent as agent

    store = _store(tmp_path)
    ref = store.write_text(
        "<h1>Failure history evidence</h1>",
        mime="text/html",
        title="fail-history",
        agent="fail_history_agent",
    )
    resolved = agent._resolve_artifact_data(store, [_artifact(ref)])
    pptx_bytes = ppt_builder.YieldReportPPTBuilder().build(
        {"lotcd": "4SS", "ref_date": "20260718", "fail_history_artifacts": resolved}
    )

    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as archive:
        slide_xml = b"".join(
            archive.read(name)
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        )
    assert b"Failure history evidence" in slide_xml


@pytest.mark.parametrize(
    "artifact",
    [
        {"type": "html", "mime": "text/html", "data": "<h1>inline</h1>"},
        {"type": "html", "mime": "text/html", "data": "file:///tmp/report.html"},
        {"type": "html", "mime": "text/html", "data": "/tmp/report.html"},
        {"type": "image", "mime": "image/png", "data": "data:image/png;base64,eA=="},
    ],
)
def test_ppt_resolver_rejects_non_reference_inputs(tmp_path, artifact):
    import ppt_export_agent as agent

    with pytest.raises(ValueError):
        agent._resolve_artifact_data(_store(tmp_path), [artifact])


def test_compact_builder_returns_in_memory_pptx_only(monkeypatch):
    import ppt_builder
    import ppt_llm_designer

    monkeypatch.setattr(
        ppt_llm_designer,
        "generate_slide_design",
        lambda state: ppt_llm_designer._default_design(state),
    )
    pptx_bytes = ppt_builder.YieldReportPPTBuilder().build_compact(
        {
            "lotcd": "4SS",
            "ref_date": "20260718",
            "unit": "weekly",
            "weeks_data": [{"week": "2026-W28", "VTH": 1.0}],
        }
    )

    assert isinstance(pptx_bytes, bytes)
    assert pptx_bytes.startswith(b"PK")
    with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as archive:
        assert "ppt/presentation.xml" in archive.namelist()


def test_ppt_export_saves_pptx_and_returns_reference_only(tmp_path, monkeypatch):
    import ppt_builder
    import ppt_export_agent as agent

    expected = b"PK\x03\x04pptx-content"
    monkeypatch.setattr(
        ppt_builder.YieldReportPPTBuilder,
        "build_compact",
        lambda self, state: expected,
    )
    store = _store(tmp_path, "export-job")
    source_ref = store.write_text(
        "<h1>source</h1>", mime="text/html", title="source", agent="yield_agent"
    )
    with artifact_scope(store):
        result = agent.ppt_export_node(
            {
                "lotcd": "4SS",
                "ref_date": "20260718",
                "weeks_data": [{"week": "2026-W28"}],
                "yield_artifacts": [_artifact(source_ref)],
            },
            {},
        )

    artifact = result["ppt_artifacts"][0]
    assert "data" not in artifact
    ref = ArtifactRef.model_validate(artifact["artifact_ref"])
    assert ref.mime == PPTX_MIME
    assert store.open(ref).read() == expected
