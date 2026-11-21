"""Register the authenticated run's domain tools in Hermes' native registry."""
import asyncio
from functools import partial
import json
import uuid


def domain_model_result(observation):
    name = observation.tool_name
    if name in {"query_db", "list_tables"} and observation.status in {"success", "partial", "empty"}:
        return {"result_id": observation.result_id, "table_id": observation.tables[0].table_id,
                **observation.scope[name], "status": observation.status, "summary": observation.summary}
    return observation.model_view()


def register_yield_tools(registry, executor, loop, *, invocation_id=None, allowed=None):
    """Bind one dedicated Hermes process to the event loop that owns its database client."""
    tools = executor.registry.model_tools()
    names = [tool["function"]["name"] for tool in tools]
    selected = set(names if allowed is None else allowed)
    unknown = selected.difference(names)
    if unknown:
        raise ValueError("unknown Yield tools: " + ", ".join(sorted(unknown)))
    if "read_result" in names:
        selected.add("read_result")
    selected = {name for name in selected if executor.registry.tools[name].effect in {"read", "artifact"}}
    collisions = sorted(name for name in selected if registry.get_entry(name) is not None)
    if collisions:
        raise ValueError("Hermes tools already registered: " + ", ".join(collisions))

    def invoke(name, args):
        call_id = invocation_id() if invocation_id is not None else None
        call = {"name": name, "args": args, "id": call_id or uuid.uuid4().hex}
        future = asyncio.run_coroutine_threadsafe(executor.execute(call), loop)
        try:
            observation = future.result()
        except Exception as exc:
            # Hermes includes uncaught handler exception text in the model response.
            return json.dumps({"status": "error", "error": type(exc).__name__})
        return json.dumps(domain_model_result(observation), ensure_ascii=False)

    for tool in tools:
        schema = tool["function"]
        name = schema["name"]
        if name in selected:
            registry.register(name=name, toolset="yield", schema=schema,
                              handler=partial(invoke, name), description=schema["description"])
    return [name for name in names if name in selected]
