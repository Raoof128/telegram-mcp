"""Live acceptance accounts and evidence (comms v0.3 Task C32; P §84–85).

Imported only when a live run is asked for (``--run-live-acceptance``). ``accounts`` reads the
operator's description of the disposable test accounts from the JSON file named by
``COMMS_LIVE_ACCOUNTS`` (adapter → identifiers only; credentials stay in the secret store);
with none, every live case reports ``NOT_CONFIGURED``. The run's report is written as evidence
by ``runner.record``; nothing in a live run is asserted by the gate.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def accounts() -> Mapping[str, Any]:
    path = os.environ.get("COMMS_LIVE_ACCOUNTS")
    if not path:
        return {}
    described = json.loads(Path(path).read_text(encoding="utf-8"))
    return {k: v for k, v in described.items() if isinstance(k, str) and isinstance(v, dict)}
