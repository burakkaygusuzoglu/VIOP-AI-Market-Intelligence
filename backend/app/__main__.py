"""Development entry point: ``python -m app``.

Exists because the event loop policy must be set before uvicorn creates the
loop, which the ``uvicorn`` CLI gives no opportunity to do. In containers this
is unnecessary and the Dockerfile calls uvicorn directly.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.runtime import configure_event_loop_policy


def main() -> None:
    configure_event_loop_policy()

    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
        log_config=None,
    )


if __name__ == "__main__":
    main()
