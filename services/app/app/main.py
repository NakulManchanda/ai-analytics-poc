from fastapi import FastAPI

from app.routers.health import router as health_router


def create_app() -> FastAPI:
    application = FastAPI(title="AI Analytics POC")
    application.include_router(health_router)
    return application


app = create_app()
