from repl_agent.runtime.worker import build_namespace, execute_code, worker_main


ROWS = [{"lot_id": "L1", "wf_id": "1", "fail_value": "3.5"}]
QUERY = {"lotcd": "P1", "start": "2026-01-01", "end": "2026-01-31", "fail_name": "OPEN"}


def test_namespace_exposes_required_analysis_libraries():
    ns = build_namespace(ROWS, QUERY)
    assert set(("df", "query", "pd", "np", "px", "go", "sm", "scipy")) <= ns.keys()


def test_variables_persist_between_executions():
    ns = build_namespace(ROWS, QUERY)
    assert execute_code(ns, "answer = 40 + 2").status == "success"
    result = execute_code(ns, "print(answer)")
    assert result.stdout == "42\n"


def test_stdout_is_bounded_and_reports_truncation():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "print('x' * 60000)")
    assert len(result.stdout) == 50000
    assert result.stdout_truncated is True


def test_stderr_is_captured_separately():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "import sys; print('warning', file=sys.stderr)")
    assert result.stdout == ""
    assert result.stderr == "warning\n"


def test_plot_is_returned_as_structured_artifact():
    ns = build_namespace(ROWS, QUERY)
    result = execute_code(ns, "fig = px.histogram(df, x='fail_value'); emit_plot(fig)")
    assert result.status == "success"
    assert result.plots[0].kind == "plotly"
    assert "data" in result.plots[0].spec


def test_runtime_error_keeps_namespace_usable():
    ns = build_namespace(ROWS, QUERY)
    failed = execute_code(ns, "raise ValueError('bad')")
    recovered = execute_code(ns, "print('still alive')")
    assert failed.error.code == "python_runtime_error"
    assert recovered.stdout == "still alive\n"


def test_syntax_error_is_classified_separately():
    result = execute_code(build_namespace(ROWS, QUERY), "if True print('bad')")

    assert result.status == "error"
    assert result.error.code == "python_syntax_error"


class ScriptedConnection:
    def __init__(self, commands):
        self.commands = iter(commands)
        self.sent = []

    def recv(self):
        return next(self.commands)

    def send(self, message):
        self.sent.append(message)


def test_worker_command_loop_sends_ready_and_json_execution_result():
    connection = ScriptedConnection(
        [
            {"type": "execute", "run_id": "run-1", "code": "print(df.iloc[0]['lot_id'])"},
            {"type": "close"},
        ]
    )

    worker_main(connection, ROWS, QUERY)

    assert connection.sent[0] == {"type": "ready"}
    assert connection.sent[1]["status"] == "success"
    assert connection.sent[1]["stdout"] == "L1\n"


def test_worker_command_loop_rejects_unknown_commands_without_execution():
    connection = ScriptedConnection(
        [
            {"type": "unknown", "run_id": "run-1", "code": "executed = True"},
            {"type": "execute", "run_id": "run-2", "code": "print('executed' in globals())"},
            {"type": "close"},
        ]
    )

    worker_main(connection, ROWS, QUERY)

    assert connection.sent[1]["error"]["code"] == "worker_protocol_error"
    assert connection.sent[2]["stdout"] == "False\n"
