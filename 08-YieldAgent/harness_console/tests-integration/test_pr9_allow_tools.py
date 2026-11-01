"""Offline allow-list validation; no app startup or tool invocation."""
import asyncio
from contextlib import asynccontextmanager

import pytest

from harness.tools.registry import domain_registry
from pr9_real_dev_server import apply_allowed_tools, resolve_allowed_tools


@pytest.mark.parametrize('raw', ['', '  ', ', ,'])
def test_empty_allow_list(raw):
    assert resolve_allowed_tools(domain_registry(), raw) == []


def test_names_are_trimmed_and_deduplicated_without_changing_handlers():
    registry = domain_registry()
    handlers = {name: spec.handler for name, spec in registry.tools.items()}
    assert resolve_allowed_tools(
        registry, ' query_yield, query_wads,query_yield, ,read_result '
    ) == ['query_yield', 'query_wads', 'read_result']
    assert all(spec.handler is handlers[name] for name, spec in registry.tools.items())


@pytest.mark.parametrize('raw', ['unknown_tool', 'query_yield,unknown_tool',
                                'run_python', 'export_report', 'query_db', 'terminal'])
def test_unknown_or_unsafe_tools_are_rejected(raw):
    with pytest.raises(ValueError):
        resolve_allowed_tools(domain_registry(), raw)


def test_all_domain_tools_follow_both_safety_conditions():
    registry = domain_registry()
    for name, spec in registry.tools.items():
        if spec.read_only is True and spec.effect == 'read':
            assert resolve_allowed_tools(registry, name) == [name]
        else:
            with pytest.raises(ValueError):
                resolve_allowed_tools(registry, name)


@pytest.mark.parametrize(('read_only', 'effect'), [
    (False, 'read'), (1, 'read'), (True, 'artifact'),
    (True, 'persistent_write'), (True, None),
])
def test_each_safety_condition_is_required(read_only, effect):
    registry = domain_registry()
    spec = registry.tools['query_yield']
    spec.read_only, spec.effect = read_only, effect
    with pytest.raises(ValueError):
        resolve_allowed_tools(registry, 'query_yield')


def test_list_tables_is_a_strict_read_only_exception(monkeypatch):
    monkeypatch.setenv('HARNESS_NO_YIELD_TOOLS', '1')

    assert resolve_allowed_tools(domain_registry(), 'list_tables') == ['list_tables']


@pytest.mark.parametrize(('raw', 'expected'), [(None, None), ('', []), (' , ', [])])
def test_absent_and_explicitly_empty_policy_are_distinct(raw, expected):
    from harness.config import Settings

    controller = type('Controller', (), {'settings': Settings()})()

    @asynccontextmanager
    async def fake_lifespan():
        apply_allowed_tools(controller, raw, [])
        yield controller

    async def scenario():
        async with fake_lifespan() as active:
            assert active is controller

    asyncio.run(scenario())

    assert controller.settings.native_allowed_tools == expected
