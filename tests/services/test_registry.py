"""comms v0.3 Task A1b: the closed service registry."""

import pytest

from comms.services.registry import ServiceRegistry, ServiceRegistryError


def test_registry_resolves_registered_names():
    reg = ServiceRegistry()
    reg.register("capability.list", list)
    assert reg.resolve("capability.list")() == []
    assert reg.names() == ("capability.list",)


def test_registry_refuses_duplicates_and_unknown_names():
    reg = ServiceRegistry()
    reg.register("a.b", lambda: 1)
    with pytest.raises(ServiceRegistryError):
        reg.register("a.b", lambda: 2)
    with pytest.raises(ServiceRegistryError):
        reg.resolve("a.c")
    for bad in ("nodot", "A.b", "a.b.c", "a b.c", ""):
        with pytest.raises(ServiceRegistryError):
            reg.register(bad, lambda: 0)
