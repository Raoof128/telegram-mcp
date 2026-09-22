"""Shared pytest configuration: the platform-gated opt-in.

Tests marked ``platform_gated`` mutate or interrogate the host (service
accounts, launchd, Touch ID, real installs). They are skipped unless the
operator asks for them with ``--run-platform-gated``, so the default suite
stays headless and side-effect free.
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-platform-gated",
        action="store_true",
        default=False,
        help="run tests that mutate or interrogate this host (macOS, admin rights)",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-platform-gated"):
        return
    skip = pytest.mark.skip(reason="needs --run-platform-gated (host-mutating)")
    for item in items:
        if "platform_gated" in item.keywords:
            item.add_marker(skip)
