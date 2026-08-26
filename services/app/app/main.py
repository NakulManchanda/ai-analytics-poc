from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from app.config import Settings
from app.events import RedisEventPublisher
from app.jobs import JobProducer
from app.llm import LLMClient, create_llm_client
from app.mcp_client import DatasetProfileMCPClient, FastMCPDatasetProfileClient
from app.orchestration import OrchestrationLoop
from app.routers.ask import create_ask_router
from app.routers.conversations import create_conversations_router
from app.routers.events import create_events_router
from app.routers.health import router as health_router
from app.routers.jobs import create_jobs_router
from app.routers.runs import RunDispatcher, create_runs_router
from app.routers.status import router as status_router
from app.state import DynamoDBStateRepository, InMemoryStateRepository, StateRepository


def create_app(
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    mcp_client: DatasetProfileMCPClient | None = None,
    state_repository: StateRepository | None = None,
    redis_client: Any = None,
    job_producer: JobProducer | None = None,
    llm_call_id_factory: Callable[[], str] | None = None,
    tool_call_id_factory: Callable[[], str] | None = None,
    orchestration_loop: OrchestrationLoop | None = None,
    run_dispatcher: RunDispatcher | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings.from_environment()
    shared_state_repo = state_repository or (
        DynamoDBStateRepository(
            table_name=resolved_settings.dynamodb_table_name,
            region_name=resolved_settings.aws_region,
        )
        if resolved_settings.dynamodb_table_name
        else InMemoryStateRepository()
    )
    application = FastAPI(title="AI Analytics POC")
    application.state.state_repository = shared_state_repo
    loop = orchestration_loop or OrchestrationLoop(
        llm_client=llm_client,
        llm_client_factory=lambda: create_llm_client(resolved_settings),
        mcp_client=mcp_client,
        mcp_client_factory=FastMCPDatasetProfileClient,
        state_repository=shared_state_repo,
        event_publisher=RedisEventPublisher(redis_client=redis_client),
        **(
            {"llm_call_id_factory": llm_call_id_factory}
            if llm_call_id_factory is not None
            else {}
        ),
        **(
            {"tool_call_id_factory": tool_call_id_factory}
            if tool_call_id_factory is not None
            else {}
        ),
    )
    application.include_router(health_router)
    application.include_router(create_ask_router(loop))
    application.include_router(
        create_runs_router(
            loop,
            **({"run_dispatcher": run_dispatcher} if run_dispatcher else {}),
        )
    )
    application.include_router(create_conversations_router(shared_state_repo))
    application.include_router(
        create_events_router(
            state_repository=shared_state_repo,
            redis_client=redis_client,
        )
    )
    application.include_router(
        create_jobs_router(
            state_repository=shared_state_repo,
            job_producer=job_producer,
            redis_client=redis_client,
        )
    )
    application.include_router(status_router)
    return application


app = create_app()
