"""Telegram read surface: the §35 service protocol and its backends.

Only ``telethon_adapter`` (Phase 4b) may import Telethon. Tool and dispatch
modules import the protocol, never a backend (spec §37; design §6.1).
"""
