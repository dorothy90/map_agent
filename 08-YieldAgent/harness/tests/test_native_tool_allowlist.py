import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest


def worker_module():
    path = Path(__file__).parents[1] / "native_worker.py"
    paths = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("allowlist_worker", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = paths


def test_worker_module_restores_import_path():
    before = list(sys.path)

    worker_module()

    assert sys.path == before


def registry():
    from harness.tools.registry import ToolRegistry, ToolSpec
    from harness.types import Contract

    value = ToolRegistry()
    for name in ("read_result", "allowed", "blocked"):
        value.add(ToolSpec(name, name, Contract, lambda *args: None))
    return value


@pytest.mark.parametrize(("configured", "expected"), [
    (None, {"read_result", "allowed", "blocked"}),
    ([], {"read_result"}),
    (["allowed"], {"read_result", "allowed"}),
])
def test_bridge_policy_filters_schemas(configured, expected):
    from harness.native_hermes import NativeBridge

    settings = SimpleNamespace(native_allowed_tools=configured)
    bridge = NativeBridge(registry(), SimpleNamespace(settings=settings))

    assert bridge.allowed == expected
    assert {item["function"]["name"] for item in bridge.schemas()} == expected


def test_minimal_context_preserves_existing_bridge_behavior():
    from harness.native_hermes import NativeBridge

    assert NativeBridge(registry(), SimpleNamespace()).allowed == {
        "read_result", "allowed", "blocked",
    }


def test_unknown_policy_name_is_rejected():
    from harness.native_hermes import NativeBridge

    context = SimpleNamespace(
        settings=SimpleNamespace(native_allowed_tools=["missing"])
    )
    with pytest.raises(ValueError, match="unknown"):
        NativeBridge(registry(), context)


def test_disallowed_direct_invocation_stops_before_executor():
    from harness.native_hermes import NativeBridge

    bridge = NativeBridge(
        registry(),
        SimpleNamespace(settings=SimpleNamespace(native_allowed_tools=["allowed"])),
    )
    bridge.executor = SimpleNamespace(
        execute=lambda call: pytest.fail("executor must not run")
    )

    with pytest.raises(PermissionError):
        asyncio.run(bridge.invoke({"name": "blocked", "id": "c", "args": {}}))


def test_restricted_worker_toolsets_exclude_builtins_and_follow_prompt_flag():
    worker = worker_module()

    assert worker.enabled_toolsets({"allowed_tools": [], "no_yield_tools": False}) == [
        "yield_backend", "yield_evidence",
    ]
    assert worker.enabled_toolsets({
        "allowed_tools": [], "no_yield_tools": True, "native_prompts": True,
    }) == ["yield_backend", "yield_evidence", "clarify"]
    assert worker.enabled_toolsets({
        "allowed_tools": ["read_result"], "no_yield_tools": True,
    }) == ["yield_backend", "yield_evidence", "clarify"]


def test_unset_worker_policy_keeps_default_toolsets():
    worker = worker_module()

    assert worker.enabled_toolsets({}) == [
        "yield_backend", "yield_evidence", "skills", "memory", "delegation",
    ]


def test_worker_registers_only_allowed_startup_schemas():
    worker = worker_module()
    registered = []
    fake_registry = SimpleNamespace(
        register=lambda **kwargs: registered.append(kwargs["name"])
    )
    startup = {
        "allowed_tools": ["read_result", "allowed"],
        "tools": [
            {"function": {"name": name, "description": name,
                          "parameters": {"type": "object"}}}
            for name in ("read_result", "allowed", "injected")
        ],
    }

    worker.register_backend_tools(startup, fake_registry, lambda name: name)

    assert registered == ["read_result", "allowed"]


def test_restricted_startup_contains_effective_names(monkeypatch, tmp_path):
    import harness.native_hermes as native
    from harness.config import Settings
    from harness.tools.registry import ToolRegistry, ToolSpec
    from harness.types import Contract

    captured = {}

    class Runs:
        async def update_one(self, query, update):
            return None

    class Stdin:
        def write(self, data):
            captured["startup"] = json.loads(data)

        async def drain(self):
            return None

    class Stdout:
        async def readline(self):
            if getattr(self, "sent", False):
                return b""
            self.sent = True
            return b'{"type":"error","message":"stop"}\n'

    async def spawn(*args, **kwargs):
        return SimpleNamespace(stdin=Stdin(), stdout=Stdout(), returncode=0)

    async def noop(*args, **kwargs):
        return None

    tools = ToolRegistry()
    for name in ("read_result", "allowed", "blocked"):
        tools.add(ToolSpec(name, name, Contract, lambda *args: None))
    settings = Settings(engine="hermes", native_allowed_tools=["allowed"])
    context = SimpleNamespace(
        settings=settings,
        run={"principal_id": "p", "session_id": "s", "run_id": "r",
             "epoch": 1, "query": "q"},
        store=SimpleNamespace(runs=Runs(), event=noop),
        remaining_seconds=lambda: 10,
        check=noop,
    )
    monkeypatch.setattr(native, "ROOT", tmp_path)
    monkeypatch.setattr(native.asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(RuntimeError, match="stop"):
        asyncio.run(native.run_native(context, tools))

    startup = captured["startup"]
    assert startup["allowed_tools"] == ["allowed", "read_result"]
    assert {item["function"]["name"] for item in startup["tools"]} == {
        "allowed", "read_result",
    }


def test_pinned_schema_resolver_cannot_reach_terminal(tmp_path):
    engine = Path(__file__).parents[1] / "engine"
    script = r'''
import json, sys
sys.path.insert(0, sys.argv[1])
from tools.registry import registry
schema = lambda name: {"name": name, "description": name,
    "parameters": {"type": "object", "properties": {}}}
registry.register(name="allowed_backend", toolset="yield_backend",
    schema=schema("allowed_backend"), handler=lambda args: "ok")
registry.register(name="read_result", toolset="yield_evidence",
    schema=schema("read_result"), handler=lambda args: "ok")
from model_tools import get_tool_definitions, _dispatch_bridge_tool
toolsets = ["yield_backend", "yield_evidence"]
raw = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True,
    skip_tool_search_assembly=True)
direct = get_tool_definitions(enabled_toolsets=toolsets, quiet_mode=True)
describe, _ = _dispatch_bridge_tool("tool_describe", {"names": ["terminal"]},
    toolsets, None)
call, underlying = _dispatch_bridge_tool("tool_call",
    {"calls": [{"name": "terminal", "arguments": {"command": "pwd"}}]},
    toolsets, None)
print(json.dumps({"raw": [x["function"]["name"] for x in raw],
    "direct": [x["function"]["name"] for x in direct],
    "describe": json.loads(describe), "call": json.loads(call),
    "underlying": underlying}))
'''
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(engine)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])

    assert set(payload["raw"]) == {"allowed_backend", "read_result"}
    assert {"tool_search", "tool_describe", "tool_call"} <= set(payload["direct"])
    assert "terminal" not in payload["direct"]
    assert "terminal" in payload["describe"].get("errors", {}) \
        or "terminal" in payload["describe"].get("not_found", [])
    assert payload["underlying"] is None
    assert "not available" in payload["call"]["error"].lower()


def test_actual_worker_constructor_receives_restricted_options(tmp_path):
    worker = Path(__file__).parents[1] / "native_worker.py"
    script = r'''
import json, runpy, sys, threading
from types import ModuleType, SimpleNamespace as NS

startup = json.loads(sys.argv[2])
registered = []
class Registry:
    def register(self, **kwargs): registered.append(kwargs["name"])
    def get_tool_names_for_toolset(self, name): return []
registry = Registry()
class DB:
    def get_session(self, session_id): return None
    def reopen_session(self, session_id): raise AssertionError
db = DB()
class Agent:
    def __init__(self, **kwargs):
        print(json.dumps({"type": "audit", "toolsets": kwargs["enabled_toolsets"],
            "registered": registered, "session_db": kwargs["session_db"] is db,
            "session_id": kwargs["session_id"]}), file=sys.__stdout__, flush=True)
        raise RuntimeError("probe complete")
tools = ModuleType("tools")
tools.skills_tool = NS()
tools.delegate_tool = NS()
tools.session_search_tool = NS()
sys.modules.update({
    "native_budget": NS(install_http_budget=lambda **kw: NS(
        exhausted_reason=None, lock=threading.Lock(),
        snapshot=lambda: dict(input_tokens=0, output_tokens=0, tokens=0, models=0))),
    "native_stop_gate": NS(NativeVerificationGate=object),
    "hermes_constants": NS(),
    "tools": tools,
    "tools.registry": NS(registry=registry),
    "run_agent": NS(AIAgent=Agent),
    "agent": NS(tool_executor=NS(_MAX_TOOL_WORKERS=8)),
    "gateway.session_context": NS(declare_stateless_channel=lambda: None),
    "hermes_state_registry": NS(acquire=lambda path: db, release_or_close=lambda value: None),
})
sys.stdin = __import__("io").StringIO(json.dumps(startup) + "\n")
runpy.run_path(sys.argv[1], run_name="__main__")
'''
    startup = {
        "run_budget_seconds": 10, "max_iterations": 1, "token_limit": 100,
        "max_tokens": 10, "base_url": "https://unused.invalid",
        "call_timeout": 1, "skills_path": str(tmp_path), "model": "synthetic",
        "api_key": "synthetic", "session_id": "restricted", "temperature": 0,
        "query": "q", "instructions": "", "history": [],
        "allowed_tools": ["read_result", "allowed"],
        "tools": [
            {"function": {"name": name, "description": name,
                          "parameters": {"type": "object"}}}
            for name in ("read_result", "allowed", "injected")
        ],
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, str(worker),
         json.dumps(startup)],
        cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=True,
        timeout=15,
    )
    audit = next(
        json.loads(line) for line in result.stdout.splitlines()
        if json.loads(line).get("type") == "audit"
    )

    assert audit == {
        "type": "audit", "toolsets": ["yield_backend", "yield_evidence"],
        "registered": ["read_result", "allowed"], "session_db": True,
        "session_id": "restricted",
    }
