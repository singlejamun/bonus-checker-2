"""Tests use only the standard library (unittest). If aiosqlite is not installed (for example in a
sandbox with no internet) a tiny stand-in is used so the SQL can still be exercised."""
import sys

try:
    import aiosqlite  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    from . import _aiosqlite_shim
    sys.modules["aiosqlite"] = _aiosqlite_shim

try:
    import discord  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    import types
    from . import _discord_stub
    _mod = types.ModuleType("discord")
    _mod.Embed = _discord_stub.Embed
    sys.modules["discord"] = _mod
