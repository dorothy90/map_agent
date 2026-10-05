import json
from urllib.parse import quote, urlencode

from .types import FinalCandidate


def candidate_argument_issues(candidate):
    return [] if candidate.answer.strip() else [{"code": "missing_answer", "field": "answer"}]


def scope_matches(actual, expected):
    if isinstance(actual, dict) and isinstance(expected, dict):
        return all(key in actual and scope_matches(actual[key], value) for key, value in expected.items())
    return actual == expected or (isinstance(expected, list) and actual in expected)


def validate_evidence(candidate, goal, observations):
    """Check stored references and their lineage, never model-copied cell values."""
    candidate = FinalCandidate.model_validate(candidate)
    issues = candidate_argument_issues(candidate)
    for result_id in dict.fromkeys(candidate.result_ids):
        pending, lineage = [result_id], {}
        while pending:
            source_id = pending.pop()
            if source_id in lineage:
                continue
            source = observations.get(source_id)
            if source is None:
                issues.append({"code": "missing_evidence" if source_id == result_id else "missing_lineage", "result_id": source_id})
                continue
            lineage[source_id] = source
            if source.get("status") not in ("success", "partial", "empty"):
                issues.append({"code": "unusable_evidence", "result_id": source_id})
            pending.extend(source.get("source_result_ids", []))
        for key, value in [*goal.get("constraints", {}).items(), *candidate.scope.items()]:
            scopes = [s.get("scope", {}) for s in lineage.values() if key in s.get("scope", {})]
            if scopes and not all(scope_matches(s[key], value) for s in scopes):
                issues.append({"code": "scope_mismatch", "result_id": result_id, "field": key})
    # Source schemas differ, and derived calculations may select a subset.
    # Missing metadata is not a contradiction. The mandatory content review
    # judges assertions absent from source scope against the rows and lineage.
    return issues


def render_results(observations, *, source_index=None):
    """Render saved values verbatim; this is not an LLM narrative fallback."""
    def cell(value):
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value if value is not None else "")
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "&#124;").replace("\n", "<br>")

    sections = []
    for obs in observations:
        if obs.get("status") not in ("success", "partial", "empty"):
            continue
        parts = ["조회·계산 결과"]
        lineage, pending, seen = [], [obs], set()
        while pending:
            source = pending.pop()
            if source["result_id"] in seen:
                continue
            seen.add(source["result_id"])
            lineage.append(source)
            pending.extend(source_index[rid] for rid in source.get("source_result_ids", []) if source_index and rid in source_index)
        scopes = list(dict.fromkeys(json.dumps(source["scope"], ensure_ascii=False, sort_keys=True) for source in lineage if source.get("scope")))
        if scopes:
            parts.append("조회 조건: " + "; ".join(cell(scope) for scope in scopes))
        if any(source.get("status") == "partial" for source in lineage):
            parts.append("일부 자료를 포함한 결과입니다. 전체 자료에 대한 결론으로 사용하지 않았습니다.")
        if obs.get("provenance", {}).get("data_origin") == "fixture":
            parts.append("가상 평가 데이터입니다.")
        for table in obs.get('tables') or [obs]:
            rows = table.get('preview_rows', [])[:20]
            columns = table.get('columns') or list(dict.fromkeys(k for row in rows for k in row))
            if table.get('title'):
                parts.append(cell(table['title']))
            if rows and columns:
                parts.append("| " + " | ".join(cell(c) for c in columns) + " |\n| " + " | ".join("---" for _ in columns) + " |\n"
                    + "\n".join("| " + " | ".join(cell(row.get(c)) for c in columns) + " |" for row in rows))
            parts.append(f"저장된 결과 {table.get('total_rows', len(rows))}행" + (f" 중 {len(rows)}행 표시" if table.get('total_rows', len(rows)) > len(rows) else ""))
            if table.get('complete') is False:
                parts.append('부분 자료: ' + cell(table.get('missing_reason') or '전체 모집단이 아닙니다.'))
        session = urlencode({"session_id": obs.get("session_id", "")})
        result_id = quote(obs["result_id"], safe="")
        parts.append(f"[조회 결과](/harness/results/{result_id}?{session})")
        artifacts = [a for a in obs.get("artifact_refs", []) if a.get("mime") != "text/plain"]
        for ref in artifacts[:5]:
            label = cell(ref.get("title") or "원본 자료").replace("[", "&#91;").replace("]", "&#93;")
            parts.append(f"[{label}](/harness/artifacts/{quote(ref['artifact_id'], safe='')}?{session})")
        if len(artifacts) > 5:
            parts.append(f"전체 산출물 {len(artifacts)}개는 산출물 목록에서 확인할 수 있습니다.")
        sections.append("\n\n".join(parts))
    return "\n\n".join(sections)
