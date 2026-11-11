"""Shared pytest marker for server-independent root tests."""


def pytest_configure(config):
    config.addinivalue_line("markers", "no_server: server-independent test")
