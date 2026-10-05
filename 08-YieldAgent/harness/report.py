"""Lossless, ordered projection of owned result data into report sections."""
from __future__ import annotations


def build_report_sections(observations, tables: dict, artifacts: dict) -> list[dict]:
    sections = []
    for obs in observations:
        if obs.status in ('error', 'cancelled'):
            continue
        status = obs.scope.get('analysis_status', 'not_run')
        if status not in ('not_run', 'no_findings', 'findings', 'partial'):
            status = 'not_run'
        if obs.status == 'partial' or obs.scope.get('complete') is False or any(not t.complete for t in obs.tables):
            status = 'partial'
        base = dict(result_ids=[obs.result_id], table_ids=[], analysis_status=status)
        sections.append(dict(base, kind='text', title=obs.tool_name,
            content={'text': obs.summary, 'scope': obs.scope}))
        for table in obs.tables:
            rows = tables[obs.result_id][table.table_id]
            if len(rows) != table.total_rows:
                raise ValueError(f'Incomplete stored table: {obs.result_id}/{table.table_id}')
            sections.append(dict(base, kind='table', title=table.title,
                table_ids=[table.table_id], content={'columns': table.columns,
                'rows': rows, 'units': table.units, 'complete': table.complete,
                'missing_reason': table.missing_reason}))
        for art in obs.artifact_refs:
            ref, data = artifacts[art['artifact_id']]
            mime = ref['mime']
            kind = 'html' if mime == 'text/html' else 'text' if mime.startswith('text/') else 'image' if mime in ('image/png','image/jpeg','image/gif') else None
            if kind is None:
                raise ValueError(f'Unsupported report artifact: {ref.get("title", "")} ({mime}); original was not included')
            content = {'data': data, 'mime': mime, 'artifact_id': art['artifact_id']} if kind != 'text' else {'text': data.decode('utf-8')}
            sections.append(dict(base, kind=kind, title=ref['title'], content=content))
    return sections
