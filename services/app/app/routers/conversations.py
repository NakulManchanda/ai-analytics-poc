from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.state import Conversation, Message, Run, RunStep, StateRepository


class ConversationResponse(BaseModel):
    conversation_id: str
    created_at: str
    updated_at: str
    title: str | None


class MessageResponse(BaseModel):
    message_id: str
    sequence: int
    role: str
    content: str
    created_at: str


class RunStepResponse(BaseModel):
    step_id: str
    sequence: int
    step_type: str
    status: str
    llm_call_id: str | None
    tool_call_id: str | None
    query_id: str | None


class RunResponse(BaseModel):
    run_id: str
    message_id: str | None
    status: str
    started_at: str
    completed_at: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    steps: list[RunStepResponse]


class ReloadedConversationResponse(ConversationResponse):
    messages: list[MessageResponse]
    runs: list[RunResponse]


def _conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        conversation_id=conversation.conversation_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
        title=conversation.title,
    )


def _message_response(message: Message) -> MessageResponse:
    return MessageResponse(
        message_id=message.message_id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
    )


def _run_response(run: Run, steps: list[RunStep]) -> RunResponse:
    return RunResponse(
        run_id=run.run_id,
        message_id=run.message_id,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        estimated_cost_usd=run.estimated_cost_usd,
        steps=[
            RunStepResponse(
                step_id=step.step_id,
                sequence=step.sequence,
                step_type=step.step_type,
                status=step.status,
                llm_call_id=step.llm_call_id,
                tool_call_id=step.tool_call_id,
                query_id=step.query_id,
            )
            for step in steps
        ],
    )


def create_conversations_router(state_repository: StateRepository) -> APIRouter:
    router = APIRouter(prefix="/api/conversations")

    @router.get("/{conversation_id}", response_model=ReloadedConversationResponse)
    def reload_conversation(conversation_id: str) -> ReloadedConversationResponse:
        conversation = state_repository.get_conversation(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        runs = state_repository.list_runs(conversation_id)
        return ReloadedConversationResponse(
            **_conversation_response(conversation).model_dump(),
            messages=[
                _message_response(message)
                for message in state_repository.list_messages(conversation_id)
            ],
            runs=[
                _run_response(run, state_repository.list_run_steps(run.run_id))
                for run in runs
            ],
        )

    return router
