import hashlib
from dataclasses import replace

from langchain_core.messages import HumanMessage
from pydantic import Field

from .instructions import load_instructions
from .checkpoints import epoch_config
from .tools.registry import ToolRegistry, ToolResult, ToolSpec
from .types import Contract
from .skills import SkillCatalog


class DelegateInput(Contract):
    question: str = Field(min_length=1, max_length=4000)
    result_ids: list[str] = Field(default_factory=list, max_length=10)
    allowed_tools: list[str] = Field(min_length=1, max_length=6)
    skill_names: list[str] = Field(default_factory=list, max_length=3)


def child_context(ctx, child_id):
    # Keep the parent's answer/review reserve and scale this child's own
    # timeout to leave two calls for investigation and two for completion.
    available = max(0, ctx.remaining_seconds() - ctx.protected_seconds)
    timeout = max(1, min(ctx.settings.call_timeout, int(available / 4)))
    return replace(ctx, depth=1, namespace="child_" + child_id + "_",
        parent_invocation_id=ctx.invocation_id,
        settings=ctx.settings.model_copy(update={'call_timeout': timeout}))


def register_delegation(registry, model_factory, checkpointer):
    async def delegate(args, ctx):
        from .graph import build_harness
        if ctx.depth:
            raise ValueError("Nested delegation is not allowed")
        child_registry = ToolRegistry()
        for name in dict.fromkeys(args.allowed_tools):
            spec = registry.tools.get(name)
            if not spec or spec.effect == 'persistent_write' or name in ('delegate_readonly', 'export_report'):
                raise ValueError("Child tools must read data or run isolated calculations")
            child_registry.add(spec)
        child_id = hashlib.sha256(ctx.invocation_id.encode()).hexdigest()[:20]
        # A recovered invocation reuses its child identity and does not spend a
        # second delegation slot. Epoch fencing protects the active executor.
        field = 'child_invocations.' + child_id
        claimed = await ctx.store.runs.update_one({'_id': ctx.run['run_id'], 'epoch': ctx.run['epoch'],
            'status': 'running', 'active': True, field: {'$exists': False},
            '$expr': {'$lt': [{'$ifNull': ['$usage.children', 0]}, 2]}},
            {'$set': {field: True}, '$inc': {'usage.children': 1}})
        if not claimed.modified_count:
            current = await ctx.check()
            if not current.get('child_invocations', {}).get(child_id):
                from .store import BudgetExceeded
                raise BudgetExceeded('children_limit')
        observations = [(await ctx.store.observation(ctx.run["principal_id"], ctx.run["session_id"], result_id)).model_dump(mode="json") for result_id in args.result_ids]
        catalog = SkillCatalog(snapshot=ctx.run.get('skill_snapshot'))
        skills = {name + '/SKILL.md': catalog.read(name) for name in args.skill_names}
        graph = build_harness(model=model_factory(), registry=child_registry, store=ctx.store, checkpointer=checkpointer,
            control=child_context(ctx, child_id), instructions=load_instructions())
        config = await epoch_config(checkpointer, ctx.run["run_id"], ctx.run["epoch"], child_id=child_id, recursion_limit=70)
        await ctx.check()
        snapshot = await graph.aget_state(config)
        initial = {"run_id": ctx.run["run_id"], "principal_id": ctx.run["principal_id"], "session_id": ctx.run["session_id"],
            "goal": {"original_request": args.question, "revision": 1, "acceptance_items": [], "constraints": {}},
            "messages": [HumanMessage(content=args.question)], "observations": observations, "active_result_ids": args.result_ids,
            "pending": [], "candidate": {}, "question": {}, "status": "running", 'loaded_skills': skills}
        result = snapshot.values if snapshot.values and not snapshot.next else await graph.ainvoke(None if snapshot.next else initial, config)
        sources = list(dict.fromkeys(obs["result_id"] for obs in result.get("observations", [])))
        summary = result.get('answer_text', result.get('answer', ''))
        return ToolResult(rows=[{"answer": summary, "status": result.get("status"), "question": result.get("question", {}), "validation_issues": result.get("validation_issues", [])}],
            summary=summary[:4000], status="success" if result.get("status") == "completed" else "partial",
            source_result_ids=sources, scope={"question": args.question, "allowed_tools": args.allowed_tools,
                'skills': [{'name': s['name'], 'version': s['version']} for s in skills.values()]},
            data_origin="fixture" if any(o.get("provenance", {}).get("data_origin") == "fixture" for o in result.get("observations", [])) else "live")
    registry.add(ToolSpec("delegate_readonly", "독립적인 읽기 조사를 위임한다. 선택한 결과와 도구만 전달한다. 최대 2개 조사, 재위임 불가. 결과를 검토한 뒤 최종 답변에 반영한다.", DelegateInput, delegate, timeout=300))
