import json
import os
import pathlib

import pytest

from whatsvault import templates as T
from whatsvault.db import connection as C
from whatsvault.db import migrations as M


def _control(tmp_path):
    return C.open_db(str(tmp_path / "c.db"), os.urandom(32))


def _mk(tmp_path):
    c = _control(tmp_path)
    M.migrate(c, "control")
    return c


def _tpl(c, status="APPROVED", defver=1):
    T.upsert_from_sync(
        c,
        [
            {
                "template_id": "tpl_1",
                "name": "order_update",
                "language": "en",
                "category": "UTILITY",
                "status": status,
                "definition_version": defver,
                "schema": {"params": 2},
            }
        ],
    )


def _prep(c, params):
    return T.prepare_template(
        c,
        conversation_id="cnv",
        account_id="acc",
        phone_number_id="PN1",
        template_id="tpl_1",
        params=params,
        now_ms=1000,
    )


def test_reaches_control_version_3(tmp_path):
    assert M.user_version(_mk(tmp_path)) >= 3


def test_non_approved_refuses(tmp_path):
    c = _mk(tmp_path)
    _tpl(c, status="PENDING")
    with pytest.raises(T.TemplateRefused) as e:
        _prep(c, [{"value": "a"}, {"value": "b"}])
    assert e.value.code == "NOT_APPROVED"


def test_param_mismatch_refuses(tmp_path):
    c = _mk(tmp_path)
    _tpl(c)
    with pytest.raises(T.TemplateRefused) as e:
        _prep(c, [{"value": "a"}])  # schema wants 2
    assert e.value.code == "PARAM_MISMATCH"


def test_approved_prepares_with_bound_digest(tmp_path):
    c = _mk(tmp_path)
    _tpl(c)
    r = _prep(c, [{"value": "a"}, {"value": "b"}])
    row = c.execute("SELECT kind, template_params_sha256 FROM drafts WHERE id=?", (r["draft_id"],)).fetchone()
    assert row[0] == "template" and row[1] is not None


def test_definition_version_changes_digest():
    a = T.params_digest("t", "en", 1, [{"value": "x"}])
    b = T.params_digest("t", "en", 2, [{"value": "x"}])
    assert a != b


def test_golden_vector():
    v = json.loads((pathlib.Path(__file__).parent / "golden" / "template_params_vectors.json").read_text())
    i = v["input"]
    assert (
        T.params_digest(i["template_name"], i["language"], i["definition_version"], i["params"]).hex()
        == v["digest_hex"]
    )
