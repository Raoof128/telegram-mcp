"""Consent-agent shell tests: build the bundle for this directory.

The bundle fixtures themselves live in the root ``conftest.py`` so the
Phase-2J join gate can use the same ones; this only makes the build
automatic for the agent suite.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _bundle_built(consent_agent_binary):
    return consent_agent_binary
