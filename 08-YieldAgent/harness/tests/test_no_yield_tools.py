import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace


def worker_module():
    path = Path(__file__).parents[1] / "native_worker.py"
    paths = list(sys.path)
    try:
        spec = importlib.util.spec_from_file_location("no_yield_tools_worker", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = paths


def model_tool_names():
    from harness.tools.registry import domain_registry

    return [tool["function"]["name"] for tool in domain_registry().model_tools()]


def test_no_yield_tools_keeps_python_tools_and_default_hermes_toolsets(monkeypatch):
    from harness.tools.registry import domain_registry

    monkeypatch.delenv("HARNESS_NO_YIELD_TOOLS", raising=False)
    monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS", raising=False)
    before = model_tool_names()
    assert before

    monkeypatch.setenv("HARNESS_NO_YIELD_TOOLS", "1")
    monkeypatch.setenv("HARNESS_WAFER_SET_TOOLS", "1")
    registry = domain_registry()
    assert model_tool_names() == [
        "read_result", "run_python", "query_db", "list_tables",
    ]
    assert registry.tools["read_result"].handler.__module__ == "harness.tools.python_tools"
    assert registry.tools["run_python"].handler.__module__ == "harness.tools.python_tools"
    assert registry.tools["query_db"].handler.__module__ == "harness.tools.query_db"
    assert registry.tools["query_db"].read_only is True
    assert registry.tools["query_db"].effect == "artifact"
    assert registry.tools["query_db"].eager is True
    assert registry.tools["list_tables"].handler.__module__ == "harness.tools.list_tables"
    assert registry.tools["list_tables"].read_only is True
    assert registry.tools["list_tables"].effect == "read"
    assert registry.tools["list_tables"].eager is True
    worker = worker_module()
    assert worker.enabled_toolsets({"no_yield_tools": True}) == [
        "yield_backend", "yield_evidence", "skills", "memory", "delegation",
        "code_execution", "file", "terminal", "todo", "session_search", "vision", "clarify",
    ]
    assert worker.enabled_toolsets({"no_yield_tools": True, "native_prompts": True}) == [
        "yield_backend", "yield_evidence", "skills", "memory", "delegation",
        "code_execution", "file", "terminal", "todo", "session_search", "vision", "clarify",
    ]
    assert worker.native_prompts_enabled({"no_yield_tools": True}) is True

    monkeypatch.delenv("HARNESS_NO_YIELD_TOOLS")
    monkeypatch.delenv("HARNESS_WAFER_SET_TOOLS")
    assert model_tool_names() == before


def test_default_hermes_toolsets_are_unchanged():
    assert worker_module().enabled_toolsets({}) == [
        "yield_backend", "yield_evidence", "skills", "memory", "delegation"
    ]


def test_no_yield_engine_toolsets_resolve_requested_tools(monkeypatch):
    worker = worker_module()
    monkeypatch.setenv("HARNESS_NO_YIELD_TOOLS", "1")
    engine = Path(__file__).parents[1] / "engine"
    sys.path.insert(0, str(engine))
    try:
        from toolsets import resolve_toolset
    finally:
        sys.path.remove(str(engine))

    names = {name for toolset in worker.enabled_toolsets({"no_yield_tools": True})
             for name in resolve_toolset(toolset)} | set(model_tool_names())

    assert names == {
        "skill_manage", "skill_view", "skills_list", "memory", "delegate_task",
        "execute_code", "patch", "read_file", "search_files", "write_file",
        "process_manage", "terminal", "todo_list", "session_search", "vision_analyze",
        "clarify", "read_result", "run_python", "query_db", "list_tables",
    }


def test_no_yield_engine_provider_comes_from_runtime_url():
    worker = worker_module()
    engine = Path(__file__).parents[1] / "engine"
    sys.path.insert(0, str(engine))
    try:
        provider = worker.engine_provider("https://openrouter.ai/api/v1")
    finally:
        sys.path.remove(str(engine))

    assert provider == "openrouter"


def test_no_yield_runtime_key_makes_vision_tool_available():
    worker = worker_module()
    entry = SimpleNamespace(check_fn=lambda: False)
    registry = SimpleNamespace(get_entry=lambda name: entry)

    worker.configure_runtime_tool_availability(
        registry, {"no_yield_tools": True, "api_key": "runtime-only"}
    )

    assert entry.check_fn() is True


def test_no_yield_runtime_refuses_background_tools_without_removing_them(monkeypatch):
    worker = worker_module()
    entries = {
        name: SimpleNamespace(handler=lambda args, _name=name, **kwargs: _name)
        for name in ("terminal", "process_manage", "execute_code")
    }
    entries["vision_analyze"] = SimpleNamespace(check_fn=lambda: False)
    registry = SimpleNamespace(get_entry=lambda name: entries.get(name))
    terminal_module = SimpleNamespace(spawn_background_process=lambda **kwargs: {"status": "running"})
    kernel_module = SimpleNamespace(execute_in_remote_kernel=lambda **kwargs: {"status": "success"})
    tools_module = ModuleType("tools")
    tools_module.terminal_tool = terminal_module
    tools_module.code_kernel_remote = kernel_module
    monkeypatch.setitem(sys.modules, "tools", tools_module)
    monkeypatch.setitem(sys.modules, "tools.terminal_tool", terminal_module)
    monkeypatch.setitem(sys.modules, "tools.code_kernel_remote", kernel_module)

    worker.configure_runtime_tool_availability(
        registry, {"no_yield_tools": True, "api_key": "runtime-only"})

    assert json.loads(entries["terminal"].handler({"command": "sleep 1", "background": True}))["error"]
    assert entries["terminal"].handler({"command": "pwd"}) == "terminal"
    assert json.loads(entries["process_manage"].handler({"action": "poll"}))["error"]
    assert terminal_module.spawn_background_process()["status"] == "error"
    assert kernel_module.execute_in_remote_kernel() is None


def test_engine_session_search_reads_profile_state_db(tmp_path):
    engine = Path(__file__).parents[1] / "engine"
    profile = tmp_path / "profile"
    script = f"""
import json
import sys
from pathlib import Path
sys.path.insert(0, {str(engine)!r})
from hermes_state import SessionDB
from tools.session_search_tool import session_search
home = Path({str(profile)!r})
home.mkdir(parents=True, exist_ok=True)
db = SessionDB(db_path=home / 'state.db')
db.create_session('old-session', 'cli', model='test')
db.append_message('old-session', 'user', 'needle-pr65 historical question')
db.append_message('old-session', 'assistant', 'historical answer')
db.close()
print(session_search(query='needle-pr65'))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
        env={"PATH": os.environ.get("PATH", ""), "HERMES_HOME": str(profile)},
        text=True, capture_output=True, check=True,
    )

    payload = json.loads(result.stdout.strip())
    assert payload["success"] is True
    assert payload["results"][0]["session_id"] == "old-session"
    assert payload["results"][0]["messages"][0]["content"] == "needle-pr65 historical question"
    assert (profile / "state.db").is_file()


def test_no_yield_tools_configures_only_isolated_ssh_environment(monkeypatch, tmp_path):
    worker = worker_module()
    key = tmp_path / "key"
    known_hosts = tmp_path / "known_hosts"
    key.write_text("key")
    known_hosts.write_text("host-key")
    home = tmp_path / "yieldrun-home"
    work_root = home / "work"
    analysis_venv = tmp_path / "analysis-venv"
    launcher = tmp_path / "yieldrun-launcher"
    workdir = work_root / "run-1"
    work_root.mkdir(parents=True)
    analysis_venv.mkdir()
    launcher.write_text("launcher")
    isolation = {
        "ssh_host": "localhost", "ssh_user": "yieldrun", "ssh_port": 22,
        "ssh_key": str(key), "known_hosts": str(known_hosts),
        "home": str(home), "work_root": str(work_root), "workdir": str(workdir),
        "analysis_venv": str(analysis_venv), "launcher": str(launcher), "sandbox": "auto",
        "run_id": "run-1", "principal_id": "alice",
    }
    monkeypatch.setenv("HARNESS_API_KEY", "secret")
    monkeypatch.setenv("ORACLE_PASSWORD", "secret")
    monkeypatch.setenv("PYTHONPATH", "/repo")
    monkeypatch.setenv("BASH_ENV", "/tmp/startup")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent")

    configured = worker.configure_agent_environment(isolation)

    assert configured == Path(isolation["workdir"])
    assert os.environ["TERMINAL_ENV"] == "ssh"
    assert os.environ["TERMINAL_CWD"] == isolation["workdir"]
    assert os.environ["TERMINAL_SSH_USER"] == "yieldrun"
    assert os.environ["VIRTUAL_ENV"] == isolation["analysis_venv"]
    assert os.environ["HERMES_WRITE_SAFE_ROOT"] == isolation["workdir"]
    for name in ("HARNESS_API_KEY", "ORACLE_PASSWORD", "PYTHONPATH", "BASH_ENV", "SSH_AUTH_SOCK"):
        assert name not in os.environ


def test_tools_payload_log_records_actual_request_names(monkeypatch, tmp_path):
    calls = []
    request_module = SimpleNamespace(
        _fire_pre_api_request_hook=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    agent_module = ModuleType("agent")
    agent_module.turn_api_request = request_module
    monkeypatch.setitem(sys.modules, "agent", agent_module)
    output = tmp_path / "tools.jsonl"

    worker = worker_module()
    worker.install_tools_payload_logger(output)
    request_module._fire_pre_api_request_hook(None, {"tools": [
        {"type": "function", "function": {"name": "query_yield"}},
        {"type": "function", "name": "web_search"},
    ]}, api_request_id="request-1")
    request_module._fire_pre_api_request_hook(None, {}, api_request_id="request-2")

    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        {"api_request_id": "request-1", "tools": ["query_yield", "web_search"]},
        {"api_request_id": "request-2", "tools": []},
    ]
    assert len(calls) == 2


def test_host_model_tools_payload_log_records_bound_names(tmp_path):
    from harness.executor import log_model_tools_payload

    output = tmp_path / "host-tools.jsonl"
    model = SimpleNamespace(kwargs={"tools": [
        {"type": "function", "function": {"name": "finish"}},
        {"type": "function", "name": "web_search"},
    ]})
    log_model_tools_payload(output, model, request_id="run:reasoning:1")

    assert json.loads(output.read_text()) == {
        "api_request_id": "run:reasoning:1", "tools": ["finish", "web_search"]
    }
