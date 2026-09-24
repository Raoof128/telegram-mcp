"""`python -m telegram_mcp.cli` keeps working (comms design §2.3). No logic here."""

from comms.transports.telegram.cli import main

__all__ = ["main"]

if __name__ == "__main__":
    main()
