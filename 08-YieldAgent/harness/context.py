import hashlib
import json
from datetime import datetime
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, messages_from_dict
from .types import ToolObservation


def serialized_size(value):
    """Local sizing proxy, not provider billing; keep its unit consistent."""
    return len(json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))) // 2 + 1


def recent_dialogue(messages):
    dialogue = [m for m in messages if m.type == "human" or
        (m.type == "ai" and not getattr(m, "tool_calls", []) and m.content)][-8:]
    return split_for_compaction(dialogue, 2400)[1] if dialogue else []


def restore_memory(previous):
    return next((r["context_memory"] for r in previous if r.get("context_memory")), {})


def session_context(previous):
    """Restore the newest working memory, replaying only subsequent turns."""
    history, observations, focus = [], {}, []
    memory_at = next((i for i, r in enumerate(previous) if r.get("context_memory")), None)
    replay = previous
    if memory_at is not None:
        item = previous[memory_at]
        memory = item["context_memory"]
        history = messages_from_dict([{"type": m["type"], "data": m} for m in memory.get("messages", [])])
        answer = item.get("answer_text", item.get("answer", ""))
        if answer and (not history or history[-1].type != "ai" or history[-1].content != answer):
            history.append(AIMessage(content=answer))
        replay = previous[:memory_at]
    for item in reversed(replay):
        history.append(HumanMessage(content=item["query"]))
        history.extend(HumanMessage(content=json.dumps(entry["value"], ensure_ascii=False)) for entry in item.get("user_inputs", []))
        history.append(AIMessage(content=item.get("answer_text", item.get("answer", ""))))
    for item in reversed(previous):
        ids = item.get("result_ids", [])
        if ids:
            focus = ids
        for obs in item.get("observations", []):
            if obs.get("run_id") == item["run_id"] or obs["result_id"] in ids:
                observations[obs["result_id"]] = obs
    return history, list(observations.values())[-100:], focus


def descriptor(observation):
    view = ToolObservation.model_validate(observation).model_view()
    view.pop("preview_rows", None)
    for table in view.get('tables', []):
        table.pop('preview_rows', None)
    view["summary"] = view["summary"][:240]
    view["column_count"] = len(view["columns"])
    view["columns"] = view["columns"][:40]
    return view


def evidence_views(observations, *, token_budget=8000):
    """Share the row budget across sources, then tables, without semantic ranking.

    Descriptors are accounted separately by the full model-input estimator. Rows
    are indivisible; an oversized row is explicitly omitted, never shortened.
    """
    sources, documents = [], {}
    for observation in {o["result_id"]: o for o in observations}.values():
        view = descriptor(observation)
        tables = observation.get('tables') or [{
            'table_id': None, 'preview_rows': observation.get('preview_rows', []),
            'total_rows': observation.get('total_rows', 0)}]
        groups = [{'table': table, 'rows': [] if observation.get('status') == 'error' else table.get('preview_rows', []),
            'shown': [], 'spent': 0} for table in tables]
        sources.append({'observation': observation, 'view': view, 'groups': groups, 'spent': 0})

    def next_row(source, group):
        index = len(group['shown'])
        if index >= len(group['rows']):
            return None
        row, key = group['rows'][index], None
        observation = source['observation']
        if row.get('doc_id'):
            body = {k: v for k, v in row.items() if k != 'score'}
            key = (observation.get('principal_id'), observation.get('session_id'),
                observation.get('provenance', {}).get('source_system', observation['tool_name']),
                row['doc_id'], hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest())
        projected = {'doc_id': row['doc_id'], 'document_ref': documents[key]} if key in documents else row
        return projected, key, serialized_size(projected)

    def take_one(source, available, groups=None):
        for group in sorted(source['groups'] if groups is None else groups, key=lambda item: item['spent']):
            item = next_row(source, group)
            if item is None or item[2] > available:
                continue
            projected, key, cost = item
            if key is not None and key not in documents:
                documents[key] = {'result_id': source['view']['result_id'], 'table_id': group['table']['table_id'], 'preview_row': len(group['shown'])}
            group['shown'].append(projected)
            group['spent'] += cost
            source['spent'] += cost
            return cost
        return 0

    remaining = max(0, token_budget)
    share = remaining // max(1, len(sources))
    for source in sources:
        allowance = share
        table_share = share // max(1, len(source['groups']))
        for group in source['groups']:
            table_allowance = table_share
            while table_allowance:
                cost = take_one(source, table_allowance, [group])
                if not cost:
                    break
                table_allowance -= cost
                allowance -= cost
                remaining -= cost
        while allowance:
            cost = take_one(source, allowance)
            if not cost:
                break
            allowance -= cost
            remaining -= cost
    # Small complete tables return their unused allocation. Spend it on the
    # least represented sources/tables, rather than the first/newest large one.
    while remaining:
        for source in sorted(sources, key=lambda item: item['spent']):
            cost = take_one(source, remaining)
            if cost:
                remaining -= cost
                break
        else:
            break

    for source in sources:
        view, observation, groups = source['view'], source['observation'], source['groups']
        view['preview_rows'] = groups[0]['shown'] if len(groups) == 1 else []
        view['truncated'] = bool(observation.get('truncated')) or any(len(group['shown']) < group['table']['total_rows'] for group in groups)
        view['omitted_preview_rows'] = sum(len(group['rows']) - len(group['shown']) for group in groups)
        view['omitted_rows'] = sum(max(0, group['table']['total_rows'] - len(group['shown'])) for group in groups)
        for table_view, group in zip(view.get('tables', []), groups):
            count = len(group['shown'])
            table_view.update(projected_rows=count, omitted_preview_rows=len(group['rows']) - count,
                omitted_rows=max(0, group['table']['total_rows'] - count), truncated=count < group['table']['total_rows'])
            if len(groups) > 1:
                table_view['preview_rows'] = group['shown']
        if view['truncated']:
            view['read_more'] = 'read_result(result_id, table_id, columns, offset, limit); run_python reads complete stored tables'
    return [source['view'] for source in sources]


def referenced_messages(messages, observations):
    """Tool results live once in evidence; native call/result pairs stay intact."""
    known = {o["result_id"]: o for o in observations}
    output = []
    for message in messages:
        if message.type == 'tool' and message.name == 'read_skill':
            try:
                payload = json.loads(message.content)
                message = message.model_copy(update={'content': json.dumps(
                    {k: v for k, v in payload.items() if k != 'content'}, ensure_ascii=False)})
            except (ValueError, TypeError, AttributeError):
                pass
        if message.type == "tool" and isinstance(message.content, str):
            try:
                payload = json.loads(message.content)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict) and payload.get("result_id") in known:
                obs = known[payload["result_id"]]
                message = message.model_copy(update={"content": json.dumps({"result_id": obs["result_id"],
                    "status": obs["status"], "source_result_ids": obs.get("source_result_ids", []),
                    "details": "See evidence/result_index; omitted data remains available with read_result."}, ensure_ascii=False)})
        output.append(message)
    return output


def build_context(state, instructions, *, final=False, evidence_tokens=8000):
    observations = list({o["result_id"]: o for o in state.get("observations", [])}.values())
    focus = list(dict.fromkeys(state.get("focus_result_ids", [])))
    active = set(state.get("active_result_ids", [o["result_id"] for o in observations if o.get("run_id") == state.get("run_id")]))
    recent = {o["result_id"] for o in observations[-24:]} | set(focus) | active
    index = [descriptor(o) for o in observations if o["result_id"] in recent]
    # Each active source/table receives a share, independent of row count or age.
    views = evidence_views([o for o in reversed(observations) if o["result_id"] in active], token_budget=evidence_tokens)
    evidence = [{k: v for k, v in view.items() if k in {"result_id", "preview_rows", "tables", "truncated", "omitted_preview_rows", "omitted_rows", "read_more"}} for view in views]
    for view in evidence:
        # Schema/title/scope are already in result_index; carry row projection once.
        view['tables'] = [{k: v for k, v in table.items() if k in {'table_id', 'preview_rows', 'total_rows', 'projected_rows', 'omitted_preview_rows', 'omitted_rows', 'truncated'}} for table in view.get('tables', [])]
    metadata = {"now": datetime.now().astimezone().isoformat(), "goal": state["goal"], "worklog": state.get("worklog", {}),
        "summary": state.get("summary", ""), "validation_issues": state.get("validation_issues", []),
        "summary_scope": "older_compacted_messages; current results and latest user instructions take precedence",
        "focus_result_ids": focus, "result_index": index, "evidence": evidence,
        "history_archives": state.get("context_archives", [])[-10:],
        "older_results": max(0, len(observations) - len(index)),
        "retrieval": "Historical results are references. select_results replaces working evidence; read_result loads needed rows/columns; recall_session recovers older dialogue. Summaries are working notes; stored data is the numerical source."}
    messages = state.get("messages", [])
    if final:
        metadata["instruction"] = "추가 조사 예산을 남기지 않고 답변을 마무리한다. 확보한 근거로 finish에 답변하고 미확인 사항은 limitations에 적는다."
        latest_request = max((i for i, message in enumerate(messages) if message.type == 'human'), default=-1)
        commentary = next((m.content for m in reversed(messages[latest_request + 1:]) if m.type == 'ai' and isinstance(m.content, str)
            and m.content.strip() and getattr(m, 'tool_calls', [])
            and all(call['name'] not in {'finish', 'ask_user'} for call in m.tool_calls)), '')
        metadata['recent_action_context'] = {'evidence_status': 'context_only_not_verified_evidence',
            'commentary': commentary[:1200], 'commentary_truncated': len(commentary) > 1200,
            'results': [{'result_id': o['result_id'], 'tool_name': o['tool_name'], 'status': o['status']} for o in observations if o['result_id'] in active][-12:]}
        messages = recent_dialogue(messages)
    skill_messages = [SystemMessage(content=json.dumps(item, ensure_ascii=False), additional_kwargs={'input_section': 'skills'})
        for item in state.get('loaded_skills', {}).values()]
    profile = [SystemMessage(content='사용자 선호 참고 자료(현재 지시 우선):\n' + state['user_profile'],
        additional_kwargs={'input_section': 'profile'})] if state.get('user_profile') else []
    return [SystemMessage(content=instructions, additional_kwargs={'input_section': 'instructions'}),
        *skill_messages, *profile,
        SystemMessage(content=json.dumps(metadata, ensure_ascii=False), additional_kwargs={'input_section': 'context'}),
        *referenced_messages(messages, observations)]


def message_groups(messages):
    groups = []
    for message in messages:
        if message.type == "tool" and groups:
            groups[-1].append(message)
        else:
            groups.append([message])
    return groups


def split_for_compaction(messages, tail_tokens):
    groups = message_groups(messages)
    last_user = max((i for i, group in enumerate(groups) if group[0].type == "human"), default=len(groups) - 1)
    cut, size = len(groups), 0
    while cut > 0:
        cost = serialized_size([m.model_dump() for m in groups[cut - 1]])
        if cut < len(groups) and size + cost > tail_tokens:
            break
        cut -= 1
        size += cost
    older, tail = groups[:cut], groups[cut:]
    if 0 <= last_user < cut:
        tail.insert(0, older.pop(last_user))
    return [m for g in older for m in g], [m for g in tail for m in g]


def memory_snapshot(state):
    messages = referenced_messages(state.get("messages", []), state.get("observations", []))
    # A successful finish is replaced by the delivered answer in session history.
    groups = [g for g in message_groups(messages) if not any(c["name"] == "finish" for c in getattr(g[0], "tool_calls", []))]
    safe = []
    for group in groups:
        calls = {c["id"] for c in getattr(group[0], "tool_calls", [])}
        replies = {m.tool_call_id for m in group if m.type == "tool"}
        if calls != replies or group[0].type == "tool":
            # Preserve the trace as historical data, never an unanswered native
            # call followed by a new human message in the next provider request.
            safe.append(AIMessage(content=json.dumps({"interrupted_tool_group": [m.model_dump(mode="json") for m in group]}, ensure_ascii=False)))
        else:
            safe.extend(group)
    return {"summary": state.get("summary", ""), "context_archives": state.get("context_archives", []),
        "messages": [m.model_dump(mode="json") for m in safe]}
