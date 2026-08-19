from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from app.config import Settings
from app.llm import LLMClient, create_llm_client
from app.mcp_client import DatasetProfileMCPClient, FastMCPDatasetProfileClient
from app.routers.ask import create_ask_router
from app.routers.events import create_events_router
from app.routers.health import router as health_router
from app.routers.status import router as status_router
from app.state import StateRepository


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    mcp_client: DatasetProfileMCPClient | None = None,
    state_repository: StateRepository | None = None,
    redis_client: Any = None,
    llm_call_id_factory: Callable[[], str] | None = None,
    tool_call_id_factory: Callable[[], str] | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    application = FastAPI(title="AI Analytics POC")
    application.include_router(health_router)
    application.include_router(
        create_ask_router(
            llm_client=llm_client,
            llm_client_factory=lambda: create_llm_client(resolved_settings),
            mcp_client=mcp_client,
            mcp_client_factory=FastMCPDatasetProfileClient,
            llm_call_id_factory=llm_call_id_factory or (lambda: f"llm_{uuid4().hex}"),
            tool_call_id_factory=tool_call_id_factory
            or (lambda: f"tool_{uuid4().hex}"),
            state_repository=state_repository,
        )
    )
    application.include_router(
        create_events_router(
            state_repository=state_repository,
            redis_client=redis_client,
        )
    )
    application.include_router(status_router)
    return application


app = create_app()
