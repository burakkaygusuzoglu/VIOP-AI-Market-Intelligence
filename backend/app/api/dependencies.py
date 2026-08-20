"""Composition root for HTTP requests.

This module and app/main.py are the only places allowed to wire concrete
adapters into the application layer. Route modules receive use cases already
constructed and never see an adapter, a session or an engine.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.application.use_cases.get_liveness import GetLiveness
from app.application.use_cases.get_system_health import GetSystemHealth


def get_system_health_use_case(request: Request) -> GetSystemHealth:
    """Return the GetSystemHealth use case assembled at startup."""
    use_case = request.app.state.get_system_health
    if not isinstance(use_case, GetSystemHealth):  # pragma: no cover - wiring guard
        raise RuntimeError("Application state is missing the GetSystemHealth use case")
    return use_case


SystemHealthUseCase = Annotated[GetSystemHealth, Depends(get_system_health_use_case)]


def get_liveness_use_case(request: Request) -> GetLiveness:
    """Return the GetLiveness use case assembled at startup."""
    use_case = request.app.state.get_liveness
    if not isinstance(use_case, GetLiveness):  # pragma: no cover - wiring guard
        raise RuntimeError("Application state is missing the GetLiveness use case")
    return use_case


LivenessUseCase = Annotated[GetLiveness, Depends(get_liveness_use_case)]
