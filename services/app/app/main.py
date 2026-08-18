from fastapi import FastAPI

from app.config import Settings
from app.llm import LLMClient, create_llm_client
from app.routers.ask import create_ask_router
from app.routers.health import router as health_router


def create_app(
    settings: Settings | None = None, llm_client: LLMClient | None = None
) -> FastAPI:
    application = FastAPI(title="AI Analytics POC")
    application.include_router(health_router)
    application.include_router(
        create_ask_router(
            llm_client or create_llm_client(settings or Settings.from_environment())
        )
    )
    return application


app = create_app()
