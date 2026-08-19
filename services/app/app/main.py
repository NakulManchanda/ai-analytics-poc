from collections.abc import Callable
from uuid import uuid4

from fastapi import FastAPI

from app.config import Settings
from app.llm import LLMClient, create_llm_client
from app.routers.ask import create_ask_router
from app.routers.health import router as health_router
from app.routers.status import router as status_router


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    llm_call_id_factory: Callable[[], str] | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    application = FastAPI(title="AI Analytics POC")
    application.include_router(health_router)
    application.include_router(
        create_ask_router(
            llm_client=llm_client,
            llm_client_factory=lambda: create_llm_client(resolved_settings),
            llm_call_id_factory=llm_call_id_factory or (lambda: f"llm_{uuid4().hex}"),
        )
    )
    application.include_router(status_router)
    return application


app = create_app()
