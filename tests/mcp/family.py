"""The per-tool checks every catalog family runs (comms v0.3 D18–D24).

Each family test module parametrizes these over its tools with, for each tool, a valid
example input and a result produced by the real service (so the output schema is proved
against what the service returns, not against a hand-written shape).
"""

from jsonschema import Draft202012Validator

from comms.core import refs
from comms.core.providers.semantics import SEMANTICS
from comms.mcp.catalog import TOOL_CATALOG
from comms.mcp.dispatch import AuthenticatedClient, Dispatcher
from comms.services.registry import ServiceRegistry

CLIENT = AuthenticatedClient(client_ref="cli_" + "a" * 26, auth_kind="cml1")


def schema_valid(spec):
    Draft202012Validator.check_schema(dict(spec.input_schema))
    Draft202012Validator.check_schema(dict(spec.output_schema))
    assert spec in TOOL_CATALOG


def output_matches(spec, result):
    errors = list(Draft202012Validator(dict(spec.output_schema)).iter_errors(result))
    assert errors == [], [e.message for e in errors]


def annotations(spec, capability=None):
    if spec.read_only:
        assert not spec.destructive and spec.idempotent and not spec.requires_request_id
        return
    assert spec.requires_request_id
    if capability is not None:
        classes = {s.retry_class for (c, _a), s in SEMANTICS.items() if c is capability}
        assert spec.destructive == ("DESTRUCTIVE_NONIDEMPOTENT" in classes)
        assert spec.idempotent == (classes == {"SET_STATE"})
        assert spec.open_world


def write_requires_request_id(spec, example):
    validator = Draft202012Validator(dict(spec.input_schema))
    assert validator.is_valid({**example, "request_id": refs.mint("request")})
    assert not validator.is_valid({k: v for k, v in example.items() if k != "request_id"})
    for bad in ("req_short", "req_" + "A" * 26, "op_" + "a" * 27, 7):
        assert not validator.is_valid({**example, "request_id": bad})


def dispatch_reaches_its_service(spec, example, result):
    seen = []
    services = ServiceRegistry()
    for other in TOOL_CATALOG:
        services.register(
            other.service, lambda c, a, name=other.service: seen.append((name, a)) or result
        )
    arguments = dict(example)
    if spec.requires_request_id:
        arguments["request_id"] = refs.mint("request")
    outcome = Dispatcher(services).call(CLIENT, spec.name, arguments)
    assert outcome.error_code is None, outcome.error_code
    assert seen == [(spec.service, arguments)] and dict(outcome.structured) == result
