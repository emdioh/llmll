"""Learner settings endpoints."""

from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_now, require_learner
from app.config import Settings
from app.llm.factory import (
    LLMConfigError,
    available_providers,
    resolve_routes,
    validate_routes,
)
from app.llm.overrides import (
    TASK_ORDER,
    OverrideIn,
    apply_overrides,
    check_task_names,
    load_overrides,
    save_overrides,
)
from app.services import learner as service
from app.store.db import get_session
from app.store.models import Learner

router = APIRouter(prefix="/api", tags=["settings"])


class SettingsOut(BaseModel):
    weekly_new_lemmas: int
    weekly_new_grammar: int
    desired_retention: float
    review_cap: int
    new_per_session: int
    production_slots: int
    drill_size: int
    timezone: str


class SettingsUpdate(BaseModel):
    weekly_new_lemmas: int | None = Field(default=None, ge=0, le=500)
    weekly_new_grammar: int | None = Field(default=None, ge=0, le=50)
    desired_retention: float | None = Field(default=None, ge=0.7, le=0.97)
    review_cap: int | None = Field(default=None, ge=1, le=500)
    new_per_session: int | None = Field(default=None, ge=0, le=20)
    production_slots: int | None = Field(default=None, ge=0, le=5)
    drill_size: int | None = Field(default=None, ge=3, le=10)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):
            raise ValueError(f"unknown IANA time zone: {value}") from None
        return value


@router.get("/settings", response_model=SettingsOut, operation_id="getSettings")
def get_settings(learner: Learner = Depends(require_learner)) -> SettingsOut:
    return SettingsOut.model_validate(service.settings_of(learner), from_attributes=True)


@router.put("/settings", response_model=SettingsOut, operation_id="updateSettings")
def update_settings(
    body: SettingsUpdate,
    session: Session = Depends(get_session),
    learner: Learner = Depends(require_learner),
) -> SettingsOut:
    changes = body.model_dump(exclude_none=True)
    return SettingsOut.model_validate(
        service.update_settings(session, learner, changes), from_attributes=True
    )


class OverrideOut(BaseModel):
    provider: str
    model: str
    structured_output: str | None = None


class LLMTaskOut(BaseModel):
    task: str
    # The route in effect now.
    provider: str
    model: str
    structured_output: str
    source: Literal["override", "config"]
    # What the environment (.env) alone gives; null when that configuration is invalid.
    config_provider: str | None
    config_model: str | None
    override: OverrideOut | None


class LLMSettingsOut(BaseModel):
    fake: bool
    live_switch: bool
    override_error: str | None
    available_providers: list[str]
    tasks: list[LLMTaskOut]


class LLMSettingsUpdate(BaseModel):
    """Partial update: tasks not listed keep their override; `null` clears one."""

    tasks: dict[str, OverrideIn | None]

    @field_validator("tasks")
    @classmethod
    def _known_tasks(cls, value: dict[str, OverrideIn | None]) -> dict[str, OverrideIn | None]:
        check_task_names(value)
        return value


def _is_fake(settings: Settings) -> bool:
    """No API key at all: the factory falls back to the simulated LLM."""
    return settings.llm_provider != "fake" and not available_providers(settings)


def _llm_settings_out(request: Request, session: Session) -> LLMSettingsOut:
    app = request.app
    settings: Settings = app.state.settings
    stored = load_overrides(session)
    try:
        config = resolve_routes(settings)
    except LLMConfigError:
        config = {}
    effective = config
    if not app.state.llm_override_error:
        try:
            effective = resolve_routes(apply_overrides(settings, stored))
        except LLMConfigError:
            effective = config
    tasks = []
    for task in TASK_ORDER:
        route, base = effective.get(task), config.get(task)
        if route is None:
            continue
        override = stored.get(task)
        tasks.append(
            LLMTaskOut(
                task=task,
                provider=str(route.provider),
                model=route.model,
                structured_output=route.structured_output,
                source="override" if override else "config",
                config_provider=str(base.provider) if base else None,
                config_model=base.model if base else None,
                override=OverrideOut(**override.model_dump()) if override else None,
            )
        )
    return LLMSettingsOut(
        fake=_is_fake(settings),
        live_switch=app.state.llm_builder is not None,
        override_error=app.state.llm_override_error,
        available_providers=available_providers(settings),
        tasks=tasks,
    )


@router.get("/settings/llm", response_model=LLMSettingsOut, operation_id="getLlmSettings")
def get_llm_settings(request: Request, session: Session = Depends(get_session)) -> LLMSettingsOut:
    return _llm_settings_out(request, session)


@router.put("/settings/llm", response_model=LLMSettingsOut, operation_id="updateLlmSettings")
def update_llm_settings(
    body: LLMSettingsUpdate,
    request: Request,
    session: Session = Depends(get_session),
) -> LLMSettingsOut:
    app = request.app
    settings: Settings = app.state.settings
    if _is_fake(settings):
        raise HTTPException(
            status_code=409,
            detail="No LLM API key is configured, so the simulated LLM is in use: "
            "set a key in .env first.",
        )
    available = available_providers(settings)
    for task, override in body.tasks.items():
        if override is not None and override.provider not in available:
            raise HTTPException(
                status_code=422,
                detail=f"task {task!r}: no API key is configured for provider "
                f"{override.provider!r} (available: {', '.join(available)}); set it in .env",
            )
    with app.state.llm_lock:
        merged = load_overrides(session)
        for task, override in body.tasks.items():
            if override is None:
                merged.pop(task, None)
            else:
                merged[task] = override
        candidate = apply_overrides(settings, merged)
        try:
            # Validate the full merged configuration before changing anything.
            new_client = app.state.llm_builder(merged) if app.state.llm_builder else None
            if new_client is None:
                validate_routes(candidate)
        except LLMConfigError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        save_overrides(session, merged, get_now(request)())
        if new_client is not None:
            app.state.llm = new_client  # requests in flight finish on the old client
        app.state.llm_override_error = None
    return _llm_settings_out(request, session)
