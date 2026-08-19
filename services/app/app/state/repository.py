from __future__ import annotations

from typing import Protocol

from app.state.models import Conversation, Job, Message, Run, RunStep


class StateError(Exception):
    """Base exception for all state repository errors."""


class EntityNotFoundError(StateError):
    """Raised when an expected entity does not exist."""


class DuplicateEntityError(StateError):
    """Raised when attempting to create an entity that already exists."""


class ConcurrencyError(StateError):
    """Raised when an update fails due to a version or condition conflict."""


class StateRepository(Protocol):
    """Abstract interface defining the durable application-state operations."""

    def create_conversation(self, conversation: Conversation) -> Conversation:
        """Persist a new conversation. Fails if conversation_id already exists."""
        ...

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        """Retrieve a conversation by its unique identifier."""
        ...

    def add_message(self, message: Message) -> Message:
        """Persist a new message within a conversation."""
        ...

    def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        """List messages belonging to a conversation ordered by sequence."""
        ...

    def create_run(self, run: Run) -> Run:
        """Persist a new run. Fails if run_id already exists."""
        ...

    def get_run(self, run_id: str) -> Run | None:
        """Retrieve a run by its unique identifier."""
        ...

    def update_run(self, run: Run) -> Run:
        """Update an existing run's status, metrics, and completion details."""
        ...

    def add_run_step(self, step: RunStep) -> RunStep:
        """Persist a new run step associated with a run."""
        ...

    def list_run_steps(self, run_id: str) -> list[RunStep]:
        """List all steps for a run ordered by sequence."""
        ...

    def create_job(self, job: Job) -> Job:
        """Persist a new async job. Fails if job_id already exists."""
        ...

    def get_job(self, job_id: str) -> Job | None:
        """Retrieve an async job by its unique identifier."""
        ...

    def update_job(self, job: Job) -> Job:
        """Update an existing async job's status, result, or timestamps."""
        ...

    def list_jobs(self, limit: int | None = None) -> list[Job]:
        """List all async jobs."""
        ...


class InMemoryStateRepository:
    """In-memory reference implementation of StateRepository for unit tests."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[Message]] = {}
        self._runs: dict[str, Run] = {}
        self._run_steps: dict[str, list[RunStep]] = {}
        self._jobs: dict[str, Job] = {}

    def create_conversation(self, conversation: Conversation) -> Conversation:
        if conversation.conversation_id in self._conversations:
            raise DuplicateEntityError(
                f"Conversation {conversation.conversation_id} already exists"
            )
        self._conversations[conversation.conversation_id] = conversation
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

    def add_message(self, message: Message) -> Message:
        if message.conversation_id not in self._conversations:
            raise EntityNotFoundError(
                f"Conversation {message.conversation_id} not found"
            )
        messages = self._messages.setdefault(message.conversation_id, [])
        if any(m.message_id == message.message_id for m in messages):
            raise DuplicateEntityError(f"Message {message.message_id} already exists")
        messages.append(message)
        messages.sort(key=lambda m: m.sequence)
        return message

    def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        messages = list(self._messages.get(conversation_id, []))
        messages.sort(key=lambda m: m.sequence)
        if limit is not None and limit > 0:
            return messages[:limit]
        return messages

    def create_run(self, run: Run) -> Run:
        if run.conversation_id not in self._conversations:
            raise EntityNotFoundError(f"Conversation {run.conversation_id} not found")
        if run.run_id in self._runs:
            raise DuplicateEntityError(f"Run {run.run_id} already exists")
        self._runs[run.run_id] = run
        return run

    def get_run(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def update_run(self, run: Run) -> Run:
        if run.run_id not in self._runs:
            raise EntityNotFoundError(f"Run {run.run_id} not found")
        self._runs[run.run_id] = run
        return run

    def add_run_step(self, step: RunStep) -> RunStep:
        if step.run_id not in self._runs:
            raise EntityNotFoundError(f"Run {step.run_id} not found")
        steps = self._run_steps.setdefault(step.run_id, [])
        if any(s.step_id == step.step_id for s in steps):
            raise DuplicateEntityError(f"Step {step.step_id} already exists")
        steps.append(step)
        steps.sort(key=lambda s: s.sequence)
        return step

    def list_run_steps(self, run_id: str) -> list[RunStep]:
        steps = list(self._run_steps.get(run_id, []))
        steps.sort(key=lambda s: s.sequence)
        return steps

    def create_job(self, job: Job) -> Job:
        if job.job_id in self._jobs:
            raise DuplicateEntityError(f"Job {job.job_id} already exists")
        self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def update_job(self, job: Job) -> Job:
        if job.job_id not in self._jobs:
            raise EntityNotFoundError(f"Job {job.job_id} not found")
        self._jobs[job.job_id] = job
        return job

    def list_jobs(self, limit: int | None = None) -> list[Job]:
        jobs = list(self._jobs.values())
        if limit is not None and limit > 0:
            return jobs[:limit]
        return jobs
