from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from langchain_core.messages import AIMessage

from artifact_context import artifact_scope, drain_saved_refs
from artifact_store import ArtifactRef, ArtifactStore


def _store(tmp_path: Path, job_id: str) -> ArtifactStore:
    return ArtifactStore(tmp_path, owner_hash="owner", job_id=job_id)


def _assert_reference_only(artifact: dict) -> ArtifactRef:
    assert "data" not in artifact
    ref = ArtifactRef.model_validate(artifact["artifact_ref"])
    assert ref.relative_path.startswith("jobs/")
    assert ref.size > 0
    return ref


def test_yield_cummap_renderer_returns_html_template_and_png_bytes(monkeypatch):
    import map_agent
    import yield_db
    from yield_viz import _build_cummap_grid_html

    monkeypatch.setattr(
        yield_db,
        "_get_period_date_ranges",
        lambda *args: [
            {"start": date(2026, 7, 1), "end": date(2026, 7, 7), "label": "W27"},
            {"start": date(2026, 7, 8), "end": date(2026, 7, 14), "label": "W28"},
        ],
    )
    monkeypatch.setattr(
        map_agent,
        "_query_wafer_data_by_date",
        lambda *args, **kwargs: [{"map_val_json": "ignored"}],
    )
    monkeypatch.setattr(map_agent, "_get_map_bounds", lambda rows: (0, 1, 0, 1))
    monkeypatch.setattr(
        map_agent,
        "_parse_wafer_bins",
        lambda value: ([0, 0, 1, 1], [0, 1, 0, 1], ["A", "A", "B", "A"]),
    )

    html, png = _build_cummap_grid_html(
        "4SS",
        date(2026, 7, 18),
        "weekly",
        2,
        [{"param": "VTH", "direction": "열화"}],
    )

    assert "__ARTIFACT_IMAGE_URL__" in html
    assert "base64" not in html
    assert png.startswith(b"\x89PNG")


def test_yield_node_externalizes_html_and_cummap_image(tmp_path, monkeypatch):
    import yield_query_agent as agent

    weeks = [
        {"week": "2026-W01", "lotcount": 1, "wfCount": 10},
        {"week": "2026-W02", "lotcount": 1, "wfCount": 10},
    ]
    monkeypatch.setattr(agent, "_fetch_periods", lambda *args: weeks)
    monkeypatch.setattr(agent, "_fetch_wafer_scatter", lambda *args: [])
    monkeypatch.setattr(agent, "_detect_anomalies", lambda rows: [])
    monkeypatch.setattr(agent, "_build_table", lambda *args, **kwargs: "table")
    monkeypatch.setattr(agent, "_build_html_table", lambda *args, **kwargs: "<html>yield</html>")
    monkeypatch.setattr(agent, "_build_scatter_html", lambda *args: "<html>scatter</html>")
    monkeypatch.setattr(
        agent,
        "_build_cummap_grid_html",
        lambda *args: ('<html><img src="__ARTIFACT_IMAGE_URL__"></html>', b"yield-png"),
    )
    monkeypatch.setattr(agent, "_analyze_with_llm", lambda *args, **kwargs: "analysis")

    store = _store(tmp_path, "yield-job")
    with artifact_scope(store):
        result = agent.yield_agent_node(
            {"lotcd": "4SS", "ref_date": "20260718", "unit": "weekly", "periods": 2},
            {},
        )
        saved = drain_saved_refs()

    assert len(result["yield_artifacts"]) == 3
    refs = [_assert_reference_only(artifact) for artifact in result["yield_artifacts"]]
    assert all(store.open(ref).read() for ref in refs)
    image_ref = next(ref for ref in saved if ref.mime == "image/png")
    assert store.open(image_ref).read() == b"yield-png"
    cummap_html = store.open(refs[-1]).read().decode()
    assert f"/jobs/yield-job/artifacts/{image_ref.artifact_id}" in cummap_html
    assert "data:image" not in cummap_html


def test_wads_node_externalizes_rendered_report(tmp_path, monkeypatch):
    import wads_agent as agent

    class FakeGraph:
        def invoke(self, *args, **kwargs):
            storage = agent._tool_payload_var.get()
            storage["reports"] = [
                {"html": "<html>WADS</html>", "lot_ids": ["ABC1234"], "groupkeys": []}
            ]
            return {"messages": [AIMessage(content="WADS result")]}

    monkeypatch.setattr(agent, "_wads_graph", FakeGraph())
    store = _store(tmp_path, "wads-job")
    with artifact_scope(store):
        result = agent.wads_agent_node(
            {"lotcd": "4SS", "wads_end_tm": "2026-07-18", "messages": []},
            {},
        )

    artifact = result["wads_artifacts"][0]
    ref = _assert_reference_only(artifact)
    assert artifact["title"] == "wads_report"
    assert b"<html>WADS</html>" in store.open(ref).read()


def test_map_node_saves_png_and_reference_only_html(tmp_path, monkeypatch):
    import map_agent as agent

    renderer_png = tmp_path / "renderer-cummap.png"
    renderer_png.write_bytes(b"\x89PNG" + b"x" * 400_000)

    monkeypatch.setattr(
        agent,
        "show_wafer_map",
        lambda **kwargs: (
            f"이미지가 생성되었습니다:\n  - Cummap: {renderer_png} (평균 Pass Rate: 98.0%)",
            [("ABC1234", 1, "2026-07-18")],
        ),
    )

    store = _store(tmp_path, "map-job")
    with artifact_scope(store):
        result = agent._handle_standard_map(
            {"lot_ids": ["ABC1234"], "wf_ids": ["1"], "map_type": "cummap", "map_oper": "PT1H"}
        )
        saved = drain_saved_refs()

    artifact = result["map_artifacts"][0]
    html_ref = _assert_reference_only(artifact)
    image_ref = next(ref for ref in saved if ref.mime == "image/png")
    html = store.open(html_ref).read().decode()
    assert store.open(image_ref).read().startswith(b"\x89PNG")
    assert f"/jobs/map-job/artifacts/{image_ref.artifact_id}" in html
    assert "base64" not in html
    assert "file://" not in html
    assert str(tmp_path) not in html
    assert not renderer_png.exists()
    checkpoint_bytes = json.dumps(result, default=str).encode()
    assert str(tmp_path).encode() not in checkpoint_bytes
    assert len(checkpoint_bytes) < 256 * 1024
