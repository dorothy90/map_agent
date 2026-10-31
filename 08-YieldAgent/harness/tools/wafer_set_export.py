"""Handle-aware compatibility adapter for report export."""
from __future__ import annotations

import copy
import hashlib
import io
import zipfile
from typing import Literal

from pydantic import Field, model_validator

from ..report import build_report_sections
from ..runtime.domain_process import run_blocking
from ..types import Contract, ResultTable
from ..wafer_set_contracts import ArtifactSelection, TableHandle
from ..wafer_set_core import build_result, resolve_many
from .artifact_tools import ExportInput, _render_ppt

MAX_TABLE_ROWS = 5_000
MAX_TABLE_CELLS = 20_000
MAX_ARTIFACT_BYTES = 30 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024


class CompatibleExportInput(Contract):
    result_ids: list[str] | None = Field(default=None, min_length=1, max_length=10)
    sources: list[TableHandle] | None = Field(default=None, min_length=1, max_length=10,
        description="보고서에 포함할 저장 표 핸들")
    artifacts: list[ArtifactSelection] = Field(default_factory=list, max_length=10,
        description="보고서에 포함할 저장 파일 선택")
    title: str = Field(default="수율 분석 보고서", max_length=100)
    format: Literal["pptx"] = Field(default="pptx", description="생성할 문서 형식")
    max_slides: int = Field(default=200, ge=1, le=200, description="허용할 최대 슬라이드 수")

    @model_validator(mode="after")
    def one_path(self):
        if (self.result_ids is None) == (self.sources is None):
            raise ValueError("Supply exactly one export path")
        if self.result_ids is not None and (self.artifacts or self.format != "pptx" or self.max_slides != 200):
            raise ValueError("Handle export fields cannot be combined with result_ids")
        return self


def _sha(data) -> str:
    payload = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(payload).hexdigest()


def _slide_count(payload: bytes) -> int:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            return sum(name.startswith("ppt/slides/slide") and name.endswith(".xml")
                       for name in archive.namelist())
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("Generated report is not a valid presentation") from exc


async def export_report(args: CompatibleExportInput, ctx):
    resolved = await resolve_many(ctx, args.sources)
    owner, session = ctx.run["principal_id"], ctx.run["session_id"]
    by_result = {}
    initialized = set()
    table_rows = {}
    manifest = []
    total_rows = total_cells = 0
    for handle, table in zip(args.sources, resolved):
        if handle.table_id == "map_payloads" or "map_val_json" in table.columns:
            raise ValueError("Raw payload tables cannot be exported to a report")
        total_rows += len(table.rows)
        total_cells += len(table.rows) * len(table.columns)
        if total_rows > MAX_TABLE_ROWS or total_cells > MAX_TABLE_CELLS:
            raise ValueError("Selected report tables exceed the size limit")
        obs = table.observation
        clone = by_result.setdefault(handle.result_id, copy.deepcopy(obs))
        if not hasattr(clone, "result_id"):
            raise ValueError("Invalid source observation")
        selected_ref = next(ref for ref in obs.tables if ref.table_id == handle.table_id)
        if handle.result_id not in initialized:
            clone.tables = []
            clone.artifact_refs = []
            initialized.add(handle.result_id)
        if not any(ref.table_id == handle.table_id for ref in clone.tables):
            clone.tables.append(copy.deepcopy(selected_ref))
        table_rows.setdefault(handle.result_id, {})[handle.table_id] = table.rows
        manifest.append({"item_type": "table", "source_result_id": handle.result_id,
            "source_table_id": handle.table_id, "source_version": table.actual_version,
            "source_artifact_id": None, "content_sha256": hashlib.sha256(
                __import__("json").dumps(table.rows, sort_keys=True, ensure_ascii=False,
                default=str).encode()).hexdigest(), "row_count": len(table.rows),
            "included": True, "missing_reason": table.missing_reason,
            "output_artifact_index": None, "output_sha256": None,
            "output_bytes": None, "slide_count": None})
    artifact_data = {}
    input_artifact_bytes = 0
    for selection in args.artifacts:
        obs = await ctx.store.observation(owner, session, selection.result_id)
        clone = by_result.setdefault(selection.result_id, copy.deepcopy(obs))
        if selection.result_id not in initialized:
            clone.tables = []
            clone.artifact_refs = []
            initialized.add(selection.result_id)
        owned = {ref["artifact_id"]: ref for ref in obs.artifact_refs}
        for artifact_id in selection.artifact_ids:
            if artifact_id not in owned:
                raise ValueError("Selected artifact is not owned by the source result")
            ref, data = await ctx.store.artifact(owner, session, artifact_id)
            if ref.get("mime") not in ("image/png", "image/jpeg", "image/gif", "text/html") and not str(ref.get("mime", "")).startswith("text/"):
                raise ValueError("Unsupported report artifact")
            payload = data.encode() if isinstance(data, str) else data
            input_artifact_bytes += len(payload)
            if input_artifact_bytes > MAX_ARTIFACT_BYTES:
                raise ValueError("Selected report artifacts exceed the byte limit")
            clone.artifact_refs.append(copy.deepcopy(owned[artifact_id]))
            artifact_data[artifact_id] = (ref, payload)
            manifest.append({"item_type": "artifact", "source_result_id": selection.result_id,
                "source_table_id": None, "source_version": None,
                "source_artifact_id": artifact_id, "content_sha256": _sha(payload),
                "row_count": None, "included": True, "missing_reason": None,
                "output_artifact_index": None, "output_sha256": None,
                "output_bytes": None, "slide_count": None})
    observations = list(by_result.values())
    sections = build_report_sections(observations, table_rows, artifact_data)
    if not sections:
        raise ValueError("No stored content was selected for export")
    if len(sections) > args.max_slides:
        raise ValueError("Estimated report slides exceed max_slides")
    payload = await run_blocking(_render_ppt, {"report_sections": sections, "title": args.title})
    if len(payload) > MAX_OUTPUT_BYTES:
        raise ValueError("Generated report exceeds the byte limit")
    slides = _slide_count(payload)
    if slides > args.max_slides:
        raise ValueError("Generated report exceeds max_slides")
    manifest.append({"item_type": "output", "source_result_id": None,
        "source_table_id": None, "source_version": None, "source_artifact_id": None,
        "content_sha256": _sha(payload), "row_count": None, "included": True,
        "missing_reason": None, "output_artifact_index": 0,
        "output_sha256": _sha(payload), "output_bytes": len(payload), "slide_count": slides})
    complete = all(table.complete for table in resolved)
    reason = next((table.missing_reason for table in resolved if not table.complete), None)
    output = ResultTable(table_id="export_manifest", title="Export manifest", rows=manifest,
        columns=list(manifest[0]), complete=complete, missing_reason=reason)
    origins = {obs.provenance.get("data_origin", "unknown") for obs in observations}
    origin = origins.pop() if len(origins) == 1 else "unknown"
    return build_result([output], source_result_ids=list(by_result),
        scope={"complete": complete}, status="success" if complete else "partial",
        summary="선택한 저장 자료를 포함한 PPTX를 생성했습니다.", data_origin=origin,
        artifacts=[{"title": args.title,
            "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "data": payload}])


async def dispatch_export(args: CompatibleExportInput, ctx, *, legacy_handler,
                          legacy_schema=ExportInput):
    if args.sources is None:
        legacy = legacy_schema.model_validate({"result_ids": args.result_ids, "title": args.title})
        return await legacy_handler(legacy, ctx)
    return await export_report(args, ctx)
