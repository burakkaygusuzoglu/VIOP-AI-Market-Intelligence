"""Event loop configuration.

psycopg's async mode cannot run on Windows' default ``ProactorEventLoop``. Any
process that opens a real database connection on Windows must install a
selector-based loop policy *before* the loop is created; otherwise every
connection fails with an InterfaceError that looks like an unreachable server.

On Linux and macOS this is a no-op, which is why the containers were unaffected.

The *shape* of the platform test below matters as much as its behaviour.

mypy resolves ``sys.platform`` to the platform it is checking for, so a branch
that cannot be taken there is eliminated statically. It deliberately stays
silent about a block that a ``sys.platform`` test skipped - that block is
intentionally conditional - but it does report a statement made unreachable
because such a test always returns before reaching it. Those are different
things, and the difference is what made the earlier guard-clause form pass on
Windows and fail under ``--platform linux``.

Two rules keep the analysis identical on both platforms, and
``tests/unit/test_runtime.py`` enforces both:

1. Every platform-dependent statement lives *inside* a branch of the
   ``sys.platform`` test, and control flow rejoins after it. Nothing that
   follows the test may depend on which branch ran.
2. The comparison is against the string literal ``"win32"``. mypy only
   special-cases ``sys.platform`` against a literal; hoisting it into a named
   constant silently disables the narrowing and turns the Windows-only API
   below into an ``attr-defined`` error on Linux, where typeshed does not
   declare it.
"""

from __future__ import annotations

import asyncio
import sys


def configure_event_loop_policy() -> bool:
    """Install a database-compatible event loop policy.

    Returns True when a policy was installed, False when the platform default
    is already suitable. Must be called before the event loop is created.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        installed = True
    else:
        installed = False
    return installed
