"""The adapter conformance runner (comms v0.3 Task C4; A18, design §C.8).

A ``Registry`` holds cases per ``(adapter, contract)``. ``run_suite`` runs every contract an
adapter advertises in ``ADAPTER_CONTRACTS``, over fakes by default. A run fails on any case
failure, on an advertised contract with no cases, on a case for a contract the adapter does not
advertise, and on any skip whose reason is not in the closed ``SKIP_REASONS`` list. Live
accounts (``--run-live-acceptance``) are reached only through ``LIVE_MODULE``, which is imported
only when a live run is asked for.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest

LIVE_MODULE = "tests.conformance.live"

# The closed list: a skip names one of these, or it is a failure.
SKIP_REASONS = frozenset(
    {
        "PROVIDER_UNSUPPORTED",  # the provider has no such operation for this actor
        "LIVE_ONLY",  # observable only against a real account (P §84–85)
        "NOT_CONFIGURED",  # live mode without the account this case needs
    }
)


class Skip(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Mode:
    live: bool
    accounts: Mapping[str, Any]  # live only: what LIVE_MODULE provides per adapter


@dataclass(frozen=True)
class Case:
    adapter: str
    contract: str
    name: str
    run: Callable[[Mode], object]


@dataclass
class Registry:
    cases: dict[tuple[str, str], list[Case]] = field(default_factory=dict)

    def case(self, adapter: str, contract: str) -> Callable[[Callable[[Mode], object]], Case]:
        def register(fn: Callable[[Mode], object]) -> Case:
            case = Case(adapter, contract, fn.__name__, fn)
            self.cases.setdefault((adapter, contract), []).append(case)
            return case

        return register


@dataclass(frozen=True)
class Failure:
    adapter: str
    contract: str
    case: str | None
    kind: str  # FAILED | UNEXPLAINED_SKIP | NO_CASES | NOT_ADVERTISED
    detail: str = ""


@dataclass(frozen=True)
class Report:
    passed: int
    skipped: Mapping[str, str]
    failures: tuple[Failure, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


def _load_live() -> Mapping[str, Any]:
    return importlib.import_module(LIVE_MODULE).accounts()


def run_suite(
    registry: Registry, contracts: Mapping[str, frozenset[str]], *, live: bool = False
) -> Report:
    mode = Mode(live=live, accounts=_load_live() if live else {})
    passed, skipped, failures = 0, {}, []
    for adapter, contract in sorted(registry.cases):
        if contract not in contracts.get(adapter, frozenset()):
            failures.append(Failure(adapter, contract, None, "NOT_ADVERTISED"))
    for adapter in sorted(contracts):
        for contract in sorted(contracts[adapter]):
            cases = registry.cases.get((adapter, contract), [])
            if not cases:
                failures.append(Failure(adapter, contract, None, "NO_CASES"))
            for case in cases:
                try:
                    case.run(mode)
                except Skip as skip:
                    if skip.reason in SKIP_REASONS:
                        skipped[case.name] = skip.reason
                    else:
                        failures.append(
                            Failure(adapter, contract, case.name, "UNEXPLAINED_SKIP", skip.reason)
                        )
                except pytest.skip.Exception as skip:
                    failures.append(
                        Failure(adapter, contract, case.name, "UNEXPLAINED_SKIP", str(skip))
                    )
                except Exception as exc:  # noqa: BLE001 -- every failure is reported, none raised
                    failures.append(
                        Failure(adapter, contract, case.name, "FAILED", type(exc).__name__)
                    )
                else:
                    passed += 1
    return Report(passed, skipped, tuple(failures))
