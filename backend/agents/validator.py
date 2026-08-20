"""
agents/validator.py
-------------------
Validator agent.

Responsibilities:
  - Validate analyzed content before report generation
  - Check relevance, completeness, consistency, gaps, weak claims
  - Return structured ValidationResult
  - Workflow uses this to PASS → writer or FAIL → analyst retry
  
Business Summary: The quality gate. 
Ensures the report won't be half-baked, off-topic, or hallucinated before writing the final document.
Logic: Compares the Analysis against the original Plan and user query. 
Checks for missing sections, logical inconsistencies, and unsupported claims. 
Assigns a score (0.0 to 1.0). 
If the score is below the threshold, 
it returns FAIL with a list of specific issues and recommendations for the Analyst.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from agents import create_chat_agent
from config import settings
from models import (
    AnalysisResult,
    ReportPlan,
    ValidationResult,
    ValidationStatus,
)
from prompts import VALIDATOR_SYSTEM, build_validator_prompt
from utils import (
    get_logger,
    log_error,
    log_event,
    message_content_to_str,
    parse_json_to_model,
)

logger = get_logger(__name__)

AGENT_NAME = "validator"


def get_validator_agent() -> Any:
    """Create the Validator agent."""
    return create_chat_agent(
        name=AGENT_NAME,
        instructions=VALIDATOR_SYSTEM,
    )


def _plan_json_for_prompt(plan: ReportPlan) -> str:
    data = plan.model_dump(mode="json", exclude_none=True)
    return json.dumps(data, indent=2, default=str)


def _analysis_json_for_prompt(analysis: AnalysisResult) -> str:
    data = analysis.model_dump(
        mode="json",
        exclude={"research", "plan"},
        exclude_none=True,
    )
    return json.dumps(data, indent=2, default=str)


def _resolve_original_query(analysis: AnalysisResult, plan: Optional[ReportPlan]) -> str:
    resolved_plan = plan or analysis.plan
    if resolved_plan and resolved_plan.analyzed_request:
        return resolved_plan.analyzed_request.original_query
    if resolved_plan and resolved_plan.title:
        return resolved_plan.title
    return ""


def _apply_pass_threshold(
    validation: ValidationResult,
    pass_score: float,
) -> ValidationResult:
    """Enforce configurable score threshold."""
    score = float(validation.score or 0.0)
    score = max(0.0, min(1.0, score))
    validation.score = score

    if score < pass_score:
        validation.status = ValidationStatus.FAIL
        note = f"Score {score:.2f} is below pass threshold {pass_score:.2f}."
        if note not in (validation.issues or []):
            validation.issues = list(validation.issues or []) + [note]
    else:
        if validation.status == ValidationStatus.PASS:
            validation.status = ValidationStatus.PASS
        elif str(validation.status).lower() == "pass":
            validation.status = ValidationStatus.PASS
        else:
            validation.status = ValidationStatus.FAIL

    return validation


def _normalize_validation(
    validation: ValidationResult,
    analysis: AnalysisResult,
    attempt: int,
    pass_score: float,
) -> ValidationResult:
    validation.analysis = analysis
    validation.attempt = max(1, attempt)
    validation.issues = validation.issues or []
    validation.missing_information = validation.missing_information or []
    validation.recommendations = validation.recommendations or []
    validation = _apply_pass_threshold(validation, pass_score=pass_score)
    return validation


async def run_validator(
    analysis: AnalysisResult,
    plan: Optional[ReportPlan] = None,
    attempt: Optional[int] = None,
    pass_score: Optional[float] = None,
    max_retries: Optional[int] = None,
    agent: Optional[Any] = None,
) -> ValidationResult:
    """Validate analysis quality / completeness."""
    if analysis is None:
        raise ValueError("AnalysisResult is required for validation.")

    resolved_plan = plan or analysis.plan
    if resolved_plan is None:
        raise ValueError(
            "ReportPlan is required for validation (pass plan or set analysis.plan)."
        )

    resolved_attempt = attempt if attempt is not None else (analysis.attempt or 1)
    threshold = (
        pass_score if pass_score is not None else settings.validation_pass_score
    )
    retries_cap = (
        max_retries
        if max_retries is not None
        else settings.max_validation_retries
    )

    original_query = _resolve_original_query(analysis, resolved_plan)

    log_event(
        "validation_started",
        title=resolved_plan.title,
        attempt=resolved_attempt,
        pass_score=threshold,
    )

    user_prompt = build_validator_prompt(
        original_query=original_query,
        plan_json=_plan_json_for_prompt(resolved_plan),
        analysis_json=_analysis_json_for_prompt(analysis),
        pass_score=threshold,
        attempt=resolved_attempt,
        max_retries=retries_cap,
    )

    chat_agent = agent or get_validator_agent()

    try:
        result = await chat_agent.run(user_prompt)
        raw_text = message_content_to_str(getattr(result, "text", None) or result)
    except Exception as exc:  # noqa: BLE001
        log_error("validation_llm_failed", exc, attempt=resolved_attempt)
        raise RuntimeError(f"Validator LLM call failed: {exc}") from exc

    try:
        validation = parse_json_to_model(raw_text, ValidationResult)
    except ValueError as exc:
        log_error(
            "validation_parse_failed",
            exc,
            raw_preview=raw_text[:500],
            attempt=resolved_attempt,
        )
        raise ValueError(
            f"Validator returned invalid structured output: {exc}"
        ) from exc

    validation = _normalize_validation(
        validation=validation,
        analysis=analysis,
        attempt=resolved_attempt,
        pass_score=threshold,
    )

    log_event(
        "validation_completed",
        title=resolved_plan.title,
        attempt=resolved_attempt,
        status=validation.status.value,
        score=validation.score,
        issues=len(validation.issues or []),
        missing=len(validation.missing_information or []),
    )
    return validation


def is_validation_pass(validation: ValidationResult) -> bool:
    """Helper for workflow routing."""
    return validation is not None and validation.status == ValidationStatus.PASS


def should_retry_validation(
    validation: ValidationResult,
    max_retries: Optional[int] = None,
) -> bool:
    """True when FAIL and another retry is allowed."""
    cap = max_retries if max_retries is not None else settings.max_validation_retries
    if validation is None:
        return False
    if validation.status == ValidationStatus.PASS:
        return False
    return validation.attempt <= cap


def run_validator_sync(
    analysis: AnalysisResult,
    plan: Optional[ReportPlan] = None,
    attempt: Optional[int] = None,
    pass_score: Optional[float] = None,
    max_retries: Optional[int] = None,
    agent: Optional[Any] = None,
) -> ValidationResult:
    """Synchronous wrapper for scripts/tests."""
    import asyncio

    return asyncio.run(
        run_validator(
            analysis=analysis,
            plan=plan,
            attempt=attempt,
            pass_score=pass_score,
            max_retries=max_retries,
            agent=agent,
        )
    )