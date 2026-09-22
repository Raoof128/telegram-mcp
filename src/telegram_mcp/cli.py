"""Synthetic-only process entry point: `telegram-mcp demo`."""

import argparse
import logging
import os
import sys

logger = logging.getLogger("telegram_mcp")


def _init_safe_logging() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logger.setLevel(logging.INFO)
    for noisy in ("uvicorn", "uvicorn.access", "uvicorn.error", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
        logging.getLogger(noisy).propagate = False


def main() -> None:
    parser = argparse.ArgumentParser(prog="telegram-mcp")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--host", default="127.0.0.1")
    demo.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()

    _init_safe_logging()

    if args.command == "demo":
        from pydantic import ValidationError

        from telegram_mcp.config import DemoConfig, validate_environment

        try:
            validate_environment(os.environ)
            config = DemoConfig(host=args.host, port=args.port)
        except (ValidationError, ValueError):
            print("telegram-mcp: invalid demo configuration.", file=sys.stderr)
            raise SystemExit(2) from None

        from telegram_mcp.server import create_app

        app = create_app(config)
        logger.info("telegram-mcp demo starting")
        print(f"telegram-mcp demo listening on {config.host}:{config.port}/mcp (synthetic build)")
        try:
            import uvicorn

            uvicorn.run(app, host=config.host, port=config.port, access_log=False)
        except OSError:
            print("telegram-mcp: failed to bind demo server.", file=sys.stderr)
            raise SystemExit(1) from None
        logger.info("telegram-mcp demo stopped")
