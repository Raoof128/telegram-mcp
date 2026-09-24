"""comms v0.3 Task C4: the adapter conformance framework (A18; design §C.8).

Every contract an adapter advertises in ADAPTER_CONTRACTS must have cases; every skip carries a
reason from a closed list; live accounts are touched only with --run-live-acceptance.
"""

import subprocess
import sys

import pytest

from tests.conformance.runner import (
    LIVE_MODULE,
    SKIP_REASONS,
    Registry,
    Skip,
    run_suite,
)

CONTRACTS = {"fake_adapter": frozenset({"delivery", "capability"})}


def _registry():
    registry = Registry()

    @registry.case("fake_adapter", "delivery")
    def sends(mode):
        assert mode.live is False

    @registry.case("fake_adapter", "capability")
    def snapshot(mode):
        return None

    return registry


def test_a_complete_registry_passes():
    report = run_suite(_registry(), CONTRACTS)
    assert report.ok and report.failures == () and report.passed == 2


def test_unexplained_skip_fails_the_run():
    registry = _registry()

    @registry.case("fake_adapter", "delivery")
    def pytest_skip(mode):
        pytest.skip("later")

    @registry.case("fake_adapter", "delivery")
    def unlisted_reason(mode):
        raise Skip("FEELS_FLAKY")

    @registry.case("fake_adapter", "capability")
    def listed_reason(mode):
        raise Skip("PROVIDER_UNSUPPORTED")

    report = run_suite(registry, CONTRACTS)
    assert not report.ok
    assert {f.case for f in report.failures} == {"pytest_skip", "unlisted_reason"}
    assert report.skipped == {"listed_reason": "PROVIDER_UNSUPPORTED"}
    assert "PROVIDER_UNSUPPORTED" in SKIP_REASONS and "FEELS_FLAKY" not in SKIP_REASONS


def test_a_failing_case_fails_the_run():
    registry = _registry()

    @registry.case("fake_adapter", "capability")
    def broken(mode):
        raise AssertionError("no")

    report = run_suite(registry, CONTRACTS)
    assert [(f.case, f.kind) for f in report.failures] == [("broken", "FAILED")]


def test_advertised_contract_without_tests_fails():
    report = run_suite(
        _registry(), {"fake_adapter": frozenset({"delivery", "capability", "admin"})}
    )
    assert not report.ok
    assert [(f.adapter, f.contract, f.kind) for f in report.failures] == [
        ("fake_adapter", "admin", "NO_CASES")
    ]


def test_a_case_for_an_unadvertised_contract_fails():
    registry = _registry()

    @registry.case("fake_adapter", "context")
    def extra(mode):
        return None

    report = run_suite(registry, CONTRACTS)
    assert [(f.contract, f.kind) for f in report.failures] == [("context", "NOT_ADVERTISED")]


def test_live_mode_is_off_by_default_and_never_imported_in_gate(pytestconfig):
    assert pytestconfig.getoption("--run-live-acceptance") is False
    run_suite(_registry(), CONTRACTS)
    assert LIVE_MODULE not in sys.modules
    # a fresh interpreter: importing the framework imports no live module either
    probe = (
        "import sys, tests.conformance.runner as r;"
        "r.run_suite(r.Registry(), {});"
        "print(r.LIVE_MODULE in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_live_mode_loads_the_live_module_only_when_asked(monkeypatch):
    loaded = []
    monkeypatch.setattr("tests.conformance.runner._load_live", lambda: loaded.append(1) or {})
    run_suite(_registry(), CONTRACTS)
    assert loaded == []
    report = run_suite(_registry(), CONTRACTS, live=True)
    assert loaded == [1] and not report.ok  # the fake delivery case refuses live mode
