"""The service error model (comms v0.3 Task D2; P §54): defined once in ``comms.core.errors``
so core can raise it without importing the services layer."""

from comms.core.errors import ERROR_CODES, MESSAGES, NAMED_ADDITIONS, CommsError

__all__ = ["ERROR_CODES", "MESSAGES", "NAMED_ADDITIONS", "CommsError"]
