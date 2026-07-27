from .process import ProcessPythonRuntime

runtime = ProcessPythonRuntime(startup_timeout_seconds=10)

__all__ = ["ProcessPythonRuntime", "runtime"]
