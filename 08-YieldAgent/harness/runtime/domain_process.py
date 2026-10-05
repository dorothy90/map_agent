"""Killable processes for trusted blocking domain functions (never generated code)."""
import asyncio
import multiprocessing
import os
import logging
from contextlib import redirect_stdout


class DomainError(RuntimeError):
    def __init__(self, message, status_code=None, retryable=False, error_type='DomainError', source_code=None):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.safe_message = message
        self.error_type = error_type
        self.source_code = source_code


def safe_error_message(exc):
    message = str(exc)
    for key, value in os.environ.items():
        if value and any(part in key.upper() for part in ('PASSWORD', 'SECRET', 'TOKEN', 'API_KEY', 'DSN', 'MONGO_URI')):
            message = message.replace(value, '[redacted]')
    return message[:1000]


def _worker(connection, function, args, kwargs):
    logging.getLogger("yield_agent.wads_tools").setLevel(logging.ERROR)
    try:
        # Legacy helpers print SQL; neither MCP stdout nor user progress receives it.
        with open(os.devnull, "w") as sink, redirect_stdout(sink):
            value = function(*args, **kwargs)
        connection.send({"ok": True, "value": value})
    except BaseException as exc:
        from ..executor import retryable
        code = getattr(exc, "status_code", None)
        detail = exc.args[0] if exc.args else exc
        source_code = getattr(detail, 'code', None)
        connection.send({"ok": False, "message": safe_error_message(exc), 'error_type': type(exc).__name__,
            'source_code': str(source_code) if source_code is not None else None,
            "status_code": code if isinstance(code, int) else None, "retryable": retryable(exc)})
    finally:
        connection.close()


async def run_blocking(function, *args, **kwargs):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, function, args, kwargs), daemon=True)
    process.start()
    child.close()
    receive = asyncio.create_task(asyncio.to_thread(parent.recv))
    try:
        result = await asyncio.shield(receive)
        if not result["ok"]:
            raise DomainError(result["message"], result["status_code"], result["retryable"], result['error_type'], result['source_code'])
        return result["value"]
    finally:
        # Complete process teardown before the executor retries or releases its slot.
        if process.is_alive():
            process.kill()
        await asyncio.shield(asyncio.to_thread(process.join))
        await asyncio.gather(receive, return_exceptions=True)
        parent.close()
        process.close()
