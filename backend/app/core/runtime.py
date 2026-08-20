"""Event loop configuration.

psycopg's async mode cannot run on Windows' default ``ProactorEventLoop``. Any
process that opens a real database connection on Windows must install a
selector-based loop policy *before* the loop is created; otherwise every
connection fails with an InterfaceError that looks like an unreachable server.

On Linux and macOS this is a no-op, which is why the containers were unaffected.
"""

from __future__ import annotations

import asyncio
import sys


def configure_event_loop_policy() -> bool:
    """Install a database-compatible event loop policy.

    Returns True when a policy was installed, False when the platform default
    is already suitable. Must be called before the event loop is created.
    """
    if sys.platform != "win32":
        return False
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return True
