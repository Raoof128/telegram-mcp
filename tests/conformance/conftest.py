"""Conformance fixtures: the run mode follows ``--run-live-acceptance`` (declared in
``tests/conftest.py``, where pytest reads options); fakes unless the operator asks."""

import pytest


@pytest.fixture
def live_acceptance(pytestconfig) -> bool:
    return bool(pytestconfig.getoption("--run-live-acceptance"))
