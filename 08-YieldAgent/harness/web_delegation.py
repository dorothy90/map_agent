"""Bind upstream delegation to the lifetime and file access of one web turn."""
from functools import wraps


def configure_web_delegation(schedule_toolsets=None):
    # Each chat worker owns a fresh interpreter and exactly one authenticated run.
    from gateway.session_finite import finite_turn_scope
    from tools import delegate_tool, delegate_tool_tasks, delegate_tool_toolsets

    def child_toolsets(parent, requested, role):
        enabled, disabled = delegate_tool_toolsets._resolve_child_toolsets(parent, requested, role)
        allowed = [name for name in enabled if name in {'yield', 'web_shared_files'}
                   and (schedule_toolsets is None or name in schedule_toolsets)]
        return allowed, disabled

    def child_images(tasks, images):
        normalized, error = delegate_tool_tasks._coerce_task_images(tasks, images)
        if not error and any(normalized):
            # Hermes forwards image attachments by reading them directly on the host.
            return None, 'Use vision_analyze on a shared yield file inside the delegated task.'
        return normalized, error

    stock_delegate = delegate_tool.delegate_task

    @wraps(stock_delegate)
    def delegate(*args, **kwargs):
        # Hermes' empty credential route inherits this parent's model, endpoint and profile.
        kwargs['credentials_cfg'] = {}
        with finite_turn_scope(True):
            return stock_delegate(*args, **kwargs)

    delegate_tool._resolve_child_toolsets = child_toolsets
    delegate_tool._coerce_task_images = child_images
    delegate_tool.delegate_task = delegate
