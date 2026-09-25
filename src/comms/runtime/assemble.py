"""The one composition root (D39-PRE Task E4).

``assemble_runtime`` turns an open ``CommsState`` and the daemon settings into the running
surface. The daemon and the smoke both call it; they differ only in the injected
``adapters_factory``: production passes ``production_adapters`` (the C33 registry over the real
provider clients), tests and ``selftest-daemon`` pass fakes. Nothing else calls
``build_comms_runtime`` (``tests/security/test_one_composition_root.py``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from comms.runtime.adapters import Adapters
from comms.runtime.comms_runtime import CommsRuntime, RemoteConfig, build_comms_runtime
from comms.runtime.operator import LegacySide
from comms.runtime.proofs import build_proofs
from comms.runtime.settings import DaemonSettings
from comms.runtime.state import CommsState

__all__ = ["AdaptersFactory", "Assembled", "assemble_runtime", "production_adapters"]

AdaptersFactory = Callable[[CommsState, DaemonSettings], Adapters]


@dataclass(frozen=True)
class Assembled:
    runtime: CommsRuntime
    adapters: Adapters

    def __repr__(self) -> str:
        return "Assembled(<redacted>)"


def production_adapters(
    *,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    archive: Any,
    telegram_session: Any = None,
    run: Any = None,
) -> AdaptersFactory:
    """The real adapter registry, bound to the daemon's clock, archive and Telethon session."""
    from comms.transports.telegram.runtime.composition import build_comms_adapters

    def build(state: CommsState, settings: DaemonSettings) -> Adapters:
        adapters: Adapters = build_comms_adapters(
            state.conn,
            state.secrets,
            settings.adapter,
            clock=clock,
            monotonic=monotonic,
            archive=archive,
            telegram_session=telegram_session,
            run=run,
        )
        return adapters

    return build


def assemble_runtime(
    state: CommsState,
    settings: DaemonSettings,
    *,
    adapters_factory: AdaptersFactory,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    legacy: LegacySide | None = None,
    reload: Callable[[], dict[str, Any]] | None = None,
    proofs: Mapping[str, Callable[[bytes], None]] | None = None,
) -> Assembled:
    adapters = adapters_factory(state, settings)
    remote = None
    if settings.remote is not None:
        remote = RemoteConfig(
            settings=settings.remote.oauth,
            client_ref=settings.remote.client,
            port=settings.remote.port,
        )
    runtime = build_comms_runtime(
        state.conn,
        state.writer,
        state.store,
        adapters,
        clock=clock,
        monotonic=monotonic,
        host=settings.host,
        local_port=settings.local_port,
        remote=remote,
        legacy=legacy,
        secrets=state.secrets,
        proofs=proofs
        if proofs is not None
        else build_proofs(phone_number_id=settings.adapter.meta_phone_number_id),
        reload=reload,
    )
    return Assembled(runtime=runtime, adapters=adapters)
