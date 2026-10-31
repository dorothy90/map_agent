"""Reuse pinned engine redaction without importing the gateway application.

Hermes gateway/run.py:577-587 delegates to agent.redact with force=True.
The engine uses absolute imports; private module-local import resolution avoids
changing sys.path/sys.modules or importing the host's unrelated `agent` package.
Only three stdlib-only engine modules are loaded; no config/env files are read.
"""
import builtins
from functools import lru_cache
from pathlib import Path
from types import ModuleType


@lru_cache(maxsize=1)
def _redactor():
    root = Path(__file__).resolve().parents[1] / 'engine'
    modules = {}
    paths = {'agent.file_safety': 'agent/file_safety.py',
             'hermes_constants': 'hermes_constants.py', 'agent.redact': 'agent/redact.py'}

    def load(name):
        if name not in modules:
            path = root / paths[name]
            module = ModuleType(name)
            module.__file__ = str(path)
            module.__builtins__ = {**vars(builtins), '__import__': engine_import}
            modules[name] = module
            exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
        return modules[name]

    def engine_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in paths:
            return load(name)
        # force=True bypasses _redact_enabled (and all profile config reads).
        if name.startswith(('agent.', 'hermes_cli.')):
            raise ImportError('Pending redaction does not load application services')
        return builtins.__import__(name, globals, locals, fromlist, level)

    return load('agent.redact').redact_sensitive_text


def redact(value):
    # Exactly gateway/run.py:587; errors propagate, never return raw input.
    return _redactor()(str(value or ''), force=True)
